Pytest Example
==============

This directory is for an example of how to customize and use the ptyest/kaizenbot framework.
The real pytest tests should be in the repository with the code being tested, and the pytest-regress-venv
repo should be a submodule included in that project.  This will allow the test framwork to be 
updated only when the user updates the submodule.

Code Changes and release policy
===============================

Code changes made to the master branch will not automatically be picked up by the projects using this
framework.  It is up to the project test stakeholders to merge, pull, or cherry pick changes committed to
the master branch.  If the framwork diverges and is not compatible with a substantial number of users
then this repo should be branched as a version.  Master is always latest, and branches are for targeted
changes for users that can't update to master due to incompatibilities.


