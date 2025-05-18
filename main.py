#!/usr/bin/env python3
import logging
import os
import time

from flask import Flask, Response
from prometheus_client import CollectorRegistry, Gauge, generate_latest, CONTENT_TYPE_LATEST
from waitress import serve

from trex.common.stats.trex_stats import StatsBatch
from trex.stl.api import STLClient, STLError

TREX_SERVER = os.environ.get('TREX_SERVER', '127.0.0.1')
TREX_PORT = int(os.environ.get('TREX_PORT', 4501))
RETRY_BACKOFF = int(os.environ.get('RETRY_BACKOFF', 5))  # seconds

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

class TRexMetricsCollector:
    def __init__(self, server, port):
        self.server = server
        self.port = port
        self.client = None
        self.connected = False
        self.extra_labels = {}
        self.server_stats = {}

    def connect(self):
        if self.client:
            try:
                self.client.disconnect()
            except Exception as e:
                logger.warning('Could not disconnect from TRex server: %s', e)
        self.client = STLClient(server=self.server, sync_port=self.port)
        self.client.connect()
        self.connected = True
        self.get_server_stats()

    def disconnect(self):
        if self.client:
            try:
                self.client.disconnect()
            except Exception as e:
                logger.warning('Could not disconnect from TRex server: %s', e)
        self.connected = False

    def get_server_stats(self):
        server_info = self.client.get_server_system_info()
        self.server_stats = {
            'dp_core_count': server_info['dp_core_count'],
            'dp_core_count_per_port': server_info['dp_core_count_per_port'],
            'port_count': server_info['port_count'],
        }
        self.extra_labels = {
            'cpu_type': server_info['core_type'],
            'per_ports': [],
        }
        for port_id in range(self.server_stats['port_count']):
            self.extra_labels['per_ports'].append(
                {
                    'nic': server_info['ports'][port_id]['description'],
                    'driver': server_info['ports'][port_id]['driver'],
                    'numa': server_info['ports'][port_id]['numa'],
                }
            )

    def get_stats(self):
        stats = {}
        try:
            if not self.connected:
                self.connect()

            self.client.global_stats.update_sync(self.client.conn.rpc)
            stats['global'] = self.client.global_stats.to_dict()

            port_stats = [self.client.ports[port_id].get_port_stats() for port_id in self.client.ports]
            StatsBatch.update(port_stats, self.client.conn.rpc)
            for port_id, stat in enumerate(port_stats):
                stats[port_id] = stat.to_dict()

        except Exception as e:
            logger.error("Failed to get stats from TRex server, trying to reconnect...: %s", e)
            self.disconnect()
        return stats

    def ensure_connected(self):
        while True:
            try:
                if not self.connected:
                    self.connect()
                # Test connection by fetching stats
                return
            except STLError as e:
                logger.error("Connection error, will try after %i: %e", RETRY_BACKOFF, e)
                self.disconnect()
                time.sleep(RETRY_BACKOFF)

# Persistent collector instance
trex_collector = TRexMetricsCollector(TREX_SERVER, TREX_PORT)

def collect_trex_stats():
    registry = CollectorRegistry()
    seen_metrics = {
            'sys_heartbeat': Gauge(name="sys_heartbeat",
                                   documentation="was connection to trex successful",
                                   registry=registry),
            'dp_core_count': Gauge(name="dp_core_count",
                                      documentation="Number of cores allocated to TRex",
                                      labelnames=['cpu_type'],
                                      registry=registry),
            'dp_core_count_per_port': Gauge(name="dp_core_count_per_port",
                                        documentation="Number of cores allocated to each dual-port",
                                        labelnames=['cpu_type'],
                                        registry=registry),
            }
    try:
        trex_collector.ensure_connected()
        cpu_type = trex_collector.extra_labels['cpu_type']
        seen_metrics['dp_core_count'].labels(cpu_type=cpu_type).set(
            trex_collector.server_stats['dp_core_count'])
        seen_metrics['dp_core_count_per_port'].labels(cpu_type=cpu_type).set(
            trex_collector.server_stats['dp_core_count_per_port'])
        seen_metrics['sys_heartbeat'].set(1)
        stats = trex_collector.get_stats()
        for key, stat in stats.items():
            if key == 'latency':
                continue

            metric_type = key

            nic = 'global'
            driver = 'global'
            numa = 'global'
            if isinstance(key, int):
                metric_type="per_port"
                nic = trex_collector.extra_labels['per_ports'][key]['nic']
                driver = trex_collector.extra_labels['per_ports'][key]['driver']
                numa = trex_collector.extra_labels['per_ports'][key]['numa']

            port_label = str(key)
            for metric_name, value in stat.items():
                if metric_name not in seen_metrics:
                    g = Gauge(
                            name=metric_name,
                            documentation=f"TRex counter {metric_name}",
                            labelnames=['port', 'metric_type', 'cpu_type', 'nic', 'driver', 'numa'],
                            registry=registry)
                    seen_metrics[metric_name] = g
                seen_metrics[metric_name].labels(port=port_label,
                                                 metric_type=metric_type,
                                                 cpu_type=cpu_type, nic=nic,
                                                 driver=driver, numa=numa).set(value)
    except STLError as e:
        logger.error("Error collecting TRex stats: %s", e)
        trex_collector.disconnect()
        seen_metrics['sys_heartbeat'].set(0)
    except Exception as e:
        logger.error("Unexpected error: %s", e)
        trex_collector.disconnect()
        seen_metrics['sys_heartbeat'].set(0)
    return registry

@app.route('/metrics')
def metrics():
    registry = collect_trex_stats()
    return Response(generate_latest(registry), mimetype=CONTENT_TYPE_LATEST)

if __name__ == '__main__':
    serve(app, host='0.0.0.0', port=8000)
