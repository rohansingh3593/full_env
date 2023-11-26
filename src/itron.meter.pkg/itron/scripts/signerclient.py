#!/usr/bin/env python3
# Copyright (c) 2022 Itron, Inc.
# Author: Benjamin Loomis
# File: signer_client.py
#
# Description: Client for autosigner web interface.
#
# Usage: signer-client.py <release id> <unsigned_1.zip> <unsigned_2.zip> [...]

import re
import sys
import requests
import time
import os
import argparse

from requests.exceptions import ConnectionError
from requests.adapters import HTTPAdapter

def main():
    def_baseurl='http://rdpsigner.itron.com/as/'
    def_username=os.getenv('signer-client_username', 'muse-dev')
    def_password=os.getenv('signer-client_password', '5mrK6EeXOnvOK9')
    def_keyset='ti-test'

    # load balanced signing server requires use of same http session so set that up
    signsession = requests.Session()
    signsession.mount(def_baseurl, HTTPAdapter(max_retries=3))

    def upload_file(session,baseurl,keyset,filename,username,password):
    ticket = ''
    qfiles = {'fileup':open(filename,"rb")}
    qargs = { 'json':'y', 'keyset':keyset, 'op':'putfile'}
    retry = 5
    isSuccess = 0
    while (0 < retry):
        isSuccess = 0
        try:
            response = session.post(baseurl,files=qfiles,data=qargs,auth=(username,password))
            status = response.status_code
            if status != 200:
                print("Error code: " + str(status) + " - check network connection")
                sys.exit(1)
        except ConnectionError as e:
            print(e)
            sys.exit(1)

        resp = response.json()
        if 'error' in resp:
            print("Error: " + resp['error'])
            sys.exit(1)
        else:
            if 'ticket' in resp:
                ticket = resp['ticket']
                print("Info: Got a ticket: " + ticket)
                to = 300
                while (0 < to):
                    status = session.post(baseurl,data={'json':'y','op':'getstatus','ticket':ticket},auth=(username,password)).json()
                    if 'error' in status:
                        print("Warn: Waiting: " + str(to) + " (" + status['error'] + ")"  + ", retrying!")
                        break
                    if 'ready' in status and status['ready']:
                        print("Info: Done! " + status['status'])
                        isSuccess = 1
                        break
                    else:
                        print("Info: Waiting: " + str(to) + " (" + status['status'] + ")")
                        time.sleep(2)
                    to = to - 1
                if(0 == to):
                    print("Error: Timed out waiting for " + ticket)
        if( isSuccess == 1 ):
            break
        retry = retry -1
    if(0 == retry):
        print("Error: retry uploading exhausted, signing failed ")
        sys.exit(1)
    return ticket

    def get_file(session,baseurl,ticket,username,password):
    combinedfile = session.post(baseurl,data={'json':'y','op':'getfile','ticket':ticket},auth=(username,password))
    if not 'content-disposition' in combinedfile.headers:
        print("Something broke, returned file has no disposition")
        exit
    else:
        outfn = combinedfile.headers['content-disposition'].split('=')[-1]
        print("Got name: " + outfn)
    # Replace hyperlong filename with decent length
        outfn_short = re.sub('(signed_Test_FWDL_ColdStart_SecureUboot)', r'sT_FWDL_CS_SecUboot', outfn)
        outfn_short = re.sub('TestSecure_Riva','',outfn_short)
        if(os.path.exists(outfn)):
            print("Error: File already exists!")
        else:
            try:
                outfp = open(outfn,"wb")
                outfp.write(combinedfile.content)
                outfp.close
            except IOError as e:
                print("I/O error({0}): {1}".format(e.errno, e.strerror))
                print("Trying a shorter filename...")
                outfp = open(outfn_short,"wb")
                outfp.write(combinedfile.content)
                outfp.close


    ##

    parser = argparse.ArgumentParser()


    parser.add_argument("--username","-u",
    help="Username", required=False,
    default=def_username, metavar="USERNAME")

    parser.add_argument("--password","-p",
    help="Password", required=False,
    default=def_password, metavar="PASSWORD")

    parser.add_argument("--keyset","-k",
    help="Keyset (ti-test, itron-test, asic-test, etc). Default: " + def_keyset, required=False,
    default=def_keyset, metavar="KEYSET")

    parser.add_argument("--signer","-s",
    help="Signer Base URL", required=False,
    default=def_baseurl, metavar="URL")

    parser.add_argument("RELEASE_ID", help="Release ID")
    parser.add_argument("UNSIGNED_ZIP", help="Unsigned Zip", nargs='+')

    args = parser.parse_args()

    relid=args.RELEASE_ID

    username = args.username
    password = args.password
    baseurl = args.signer
    keyset = args.keyset


    # production signing is a manual process so
    # inform user of file that needs to be signed
    # and wait for a signed file to show up
    # this depends on the waitforit helper script
    if keyset=="itron-release" or keyset=='asic-release':
        fn = args.UNSIGNED_ZIP[0]
        print("file to be signed: " + fn)
        os.system("$HOME/bin/wait4file.sh signed*zip")
        exit(0)


    tickets=''
    # Verify input files
    for fn in args.UNSIGNED_ZIP:
    if not os.path.exists(fn):
        print("Error: file " + fn + " doesn't exist")
        sys.exit(1)

    # Sign each input file
    for fn in args.UNSIGNED_ZIP:
    print("Signing: " + fn)
    ticket = upload_file(signsession,baseurl,keyset,fn,username,password)
    if(not ticket or '' == ticket):
        print("Didn't get a ticket for " + fn)
        sys.exit(1)
    else:
        get_file(signsession,baseurl,ticket,username,password)
        tickets += (',' if tickets else '') + ticket

    # Combine only if there are more than one input files
    print("All tickets: " + tickets)
    if(1 < len(args.UNSIGNED_ZIP)):
    print("Combining " + str(len(sys.argv) - 2) + " packages")
    combr = signsession.post(baseurl,data={'json':'y','op':'combine','tickets':tickets, 'relid':relid},auth=(username,password))
    status = combr.json()
    if 'error' in status:
        print("Error: " + status['error'])
        exit
    else:
        if 'ticket' in status:
            print("combine queued: " + status['ticket'])
            ticket = status['ticket']
            to = 30
            while (0 < to):
                status = signsession.post(baseurl,data={'json':'y','op':'getstatus','ticket':ticket},auth=(username,password)).json()
                if 'error' in status:
                print("Error: " + status['error'])
                exit
                if 'ready' in status and status['ready']:
                print("Done! " + status['status'])
                get_file(signsession,baseurl,ticket,username,password)
                break
                else:
                print("Waiting: " + str(to) + " (" + status['status'] + ")")
                time.sleep(2)
                to = to - 1
            if(1 == to):
                print("Timed out!")
                exit
        else:
            print("error: didn't get a ticket")
            exit
    else:
    print("Only one input file, will not be combined")

if __name__=='__main__':
    main()

