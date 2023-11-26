from itron.meter import AsMan
import kaizenbot.connection
import os
import json
import pytest

@pytest.mark.xfail
def test_always_fails(logger):
    logger.info("Test that always fails")
    assert False, "this test is supposed to fail"

def test_always_pass(logger):
    logger.info("Test that always passes")
    assert True


def test_file_artifact(logger,tmp_path):
    logger.info("Test that needs supporting files can create in workdir")

    outfilename = os.path.join(tmp_path, "test.json")

    # current directory is the test directory
    infilename = "test_file.json"

    logger.info("modified file: %s", outfilename)
    with open(infilename, "r") as infile:
            with open(outfilename, "w") as outfile:
                outfile.write(infile.read())
    

    jsondata = json.json.load(outfilename)
    assert jsondata['Test'], "could not load copy of json file"
    
def test_kaizenbot(logger):
    c = kaizenbot.connection.Connection()
