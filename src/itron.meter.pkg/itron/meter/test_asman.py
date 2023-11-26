import pytest

from itron.meter import AsMan
import re

def test_asman():
    bld=AsMan.get_build('1.5.317.0')
    assert bld
    assert re.search(".zip$", bld)
    bld=AsMan.get_build('1.3.470.0')
    assert bld