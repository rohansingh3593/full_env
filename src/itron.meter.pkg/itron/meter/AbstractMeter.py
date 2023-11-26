#!/usr/bin/env python3


import abc

class AbstractMeter(abc.ABC):
    """ class for manipulating a virtual or real meter"""

    @abc.abstractclassmethod
    def __init__(self,*args):
        pass

    @abc.abstractclassmethod
    def __enter__(self):
        #ttysetattr etc goes here before opening and returning the file object
        return self

    @abc.abstractclassmethod
    def __exit__(self, _type, value, traceback):
        #Exception handling here
        pass

    @abc.abstractclassmethod
    def coldstart(self, fwver):
        pass

    @abc.abstractclassmethod
    def install(self, package_file):
        pass

    @abc.abstractclassmethod
    def download_db(self, location):
        pass

