#!/usr/bin/env python3
# Copyright (c) 2022 rohan, Inc.
# Author: Benjamin Loomis
# File: sign_distdir.py
#
# Description: Pack and sign a release
#
# Usage: signer-distdir.py <-i releaseid> <-d directory> [-t LOADER_TYPE]

import os
import zipfile
import argparse
import json
from datetime import datetime
import pprint


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("INPUT", help="Input image")
    parser.add_argument("OUTPUT", help="Output zip")

    args = parser.parse_args()

    pprint.pprint(args)

    # Check that we have the required files
    if os.path.exists(args.OUTPUT):
        print("Error: Output file  " + args.OUTPUT + " already exists!")
        exit(1)

    if not os.path.exists(args.INPUT):
        print("Error: Input file  " + args.INPUT + " does not exist!")
        exit(1)

    zfile = zipfile.ZipFile(args.OUTPUT, "w")

# Build the signer control file
    timestamp = datetime.utcnow().replace(microsecond=0).isoformat() + 'Z'
    signer_control = {'version': '1.0', 'md': 'sha256', 'timestamp': timestamp}
    signer_control['ota'] = []

    signer_control['ota'].append({'input': args.INPUT, 'output': args.OUTPUT})
    zfile.write(args.INPUT, os.path.basename(args.INPUT))

    zfile.writestr("signer-control.json", json.dumps(signer_control))

    zfile.close()
    print("Done")
    exit(0)


if __name__ == "__main__":
    main()
