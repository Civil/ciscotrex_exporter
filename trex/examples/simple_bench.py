import trex.examples.stl.stl_path as stl_path
from trex.stl.api import *

import time
import json
from pprint import pprint
import argparse
import sys
import os

def simple_test (server, rate):
    # create client
    c = STLClient(server = server)
    passed = True

    try:
        print(f"Trying to connect to {server}")
        # connect to server
        c.connect()

        # take all the ports
        c.reset()

        # map ports - identify the routes
        table = stl_map_ports(c)

        dir_0 = [x[0] for x in table['bi']]
        dir_1 = [x[1] for x in table['bi']]
        
        print("Mapped ports to sides {0} <--> {1}".format(dir_0, dir_1))

        # load IMIX profile
        profile = STLProfile.load_py(os.path.join(stl_path.STL_PROFILES_PATH, 'simple.py'))
        streams = profile.get_streams()

        # add both streams to ports
        c.add_streams(streams, ports = dir_0)
        c.add_streams(streams, ports = dir_1)
        
        # clear the stats before injecting
        c.clear_stats()

        # choose rate and start traffic for 10 seconds
        duration = 10
        print(f"Injecting {dir_0} <--> {dir_1} of {rate} for {duration} seconds")

        c.start(ports = (dir_0 + dir_1), mult=rate, duration = duration, total = True)
        
        # block until done
        c.wait_on_traffic(ports = (dir_0 + dir_1))

        # read the stats after the test
        stats = c.get_stats()

        # use this for debug info on all the stats
        pprint(stats)

        # sum dir 0
        dir_0_opackets = sum([stats[i]["opackets"] for i in dir_0])
        dir_0_ipackets = sum([stats[i]["ipackets"] for i in dir_0])

        # sum dir 1
        dir_1_opackets = sum([stats[i]["opackets"] for i in dir_1])
        dir_1_ipackets = sum([stats[i]["ipackets"] for i in dir_1])


        lost_0 = dir_0_opackets - dir_1_ipackets
        lost_1 = dir_1_opackets - dir_0_ipackets

        print("\nPackets injected from {0}: {1:,}".format(dir_0, dir_0_opackets))
        print("Packets injected from {0}: {1:,}".format(dir_1, dir_1_opackets))

        print("\npackets lost from {0} --> {1}:   {2:,} pkts".format(dir_0, dir_0, lost_0))
        print("packets lost from {0} --> {1}:   {2:,} pkts".format(dir_1, dir_1, lost_1))

        if c.get_warnings():
            print("\n\n*** test had warnings ****\n\n")
            for w in c.get_warnings():
                print(w)

        if (lost_0 <= 0) and (lost_1 <= 0) and not c.get_warnings(): # less or equal because we might have incoming arps etc.
            passed = True
        else:
            passed = False


    except STLError as e:
        passed = False
        print(e)
        sys.exit(1)

    finally:
        c.disconnect()

    if passed:
        print("\nTest has passed :-)\n")
    else:
        print("\nTest has failed :-(\n")

parser = argparse.ArgumentParser(description="Example for TRex Stateless, sending IMIX traffic")
parser.add_argument('-t', '--trex',
                    dest='trex',
                    help='Remote trex address',
                    default='127.0.0.1',
                    type = str)
parser.add_argument('-r', '--rate',
                    dest='rate',
                    help='Target packet rate in kpps',
                    type=int,
                    required=True)
parser.add_argument('-d', '--duration',
                    dest='duration'
                    help='duration of each ramp in seconds',
                    type=int,
                    default=10)
parser.add_argument('-s', '--steps',
                    dest='step'
                    help='how many steps we should do before reaching the goal',
                    type=int,
                    default=10)

args = parser.parse_args()

# run the tests
simple_test(args.server, args.rate)

