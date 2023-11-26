from bs4 import BeautifulSoup
import requests
import os
from glob import glob as original_glob
import re

OWI_URL = 'http://vm-rdgbuild-03.itron.com/OWI_Builds/'
OWI_MOUNT = '/mnt/ral-rdgbuild-03/'


def glob(path, recurse=False):
    """ works like glob.glob() for URL if mount point
        is not present """
    dirs = original_glob(path)
    if not dirs:
        if path.startswith(OWI_MOUNT):
            dir = path.replace(OWI_MOUNT, '')
        elif path.startswith(OWI_URL):
            dir = path.replace(OWI_URL, '')
        else:
            dir = path

        if dir.endswith('/'):
            dir = dir[:-1]

        matches = []
        match_levels = [re.compile(x.replace('*','.+')) for x in dir.split('/')]

        def splitup(path):
            paths = path.replace(OWI_URL, '').split('/')
            if paths[-1] == '':
                del paths[-1]
            return paths

        def dirmatch(path):
            paths = splitup(path)
            for idx in range(len(paths)):
                if idx < len(match_levels) and not match_levels[idx].fullmatch(paths[idx]):
                    return False
            return True

        depth = len(dir.split('/')) if not recurse else 999
        for item in walk_url(OWI_URL, depth=depth, filter=dirmatch):
            matches.append(item)

        dirs = [x for x in matches if len(splitup(x)) >= len(match_levels)]

    return dirs

def get_url_items(url):
    if not url.endswith('/'):
        url += '/'
    page = requests.get(url).text
    soup = BeautifulSoup(page, 'html.parser')
    items = []
    for node in soup.find_all('a'):
        ref = node.get('href')
        if ref and '?' not in ref and ref != '/':
            items.append(ref)
    dirs = [url + item for item in items if item.endswith('/')]
    files = [url + item for item in items if not item.endswith('/')]
    return dirs, files

def walk(path, depth=99, filter=lambda x: True):
    if os.path.exists(path):
        yield from os.walk(path)
    else:
        yield from walk_url(path, depth, filter)

def walk_url(path, depth, filter):
    path = path.replace(OWI_MOUNT, OWI_URL)
    dirs, files = get_url_items(path)
    for file in files:
        if filter(file):
            yield file

    depth -= 1
    for dir in dirs:
        if filter(dir):
            yield dir
            if depth:
                yield from walk_url(dir, depth, filter)

def listdir(path):
    if path.startswith('http://'):
        dirs, files = get_url_items(path)
        dirs.extend(files)
        return [ os.path.basename(name[:-1])+'/' if name.endswith('/') else os.path.basename(name)
            for name in dirs]
    else:
        return os.listdir(path)
