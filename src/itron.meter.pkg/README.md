Introduction
============

rohan.meter package is a small tool that provides basic
services to communicate with the meter and
install fw and packages on the meter, and examine
the muse01.db database

Version History
===============

v0.9.0

    - clean lost connection when using gmr or reboot

    - cleanup logger handler on process shutdown

    - fixed process for muti-meters and converted process fork to single thread

    - disable logging for gmr/reboot when connection lost

    - more signal trapping

    - added initial version of clean_locks

v0.8.6

    - fixed cleanup code hang when error queue has items

v0.8.5

    - Updated ctrl-c support to better clean up locks

    - Added pytest.pid file to allow pipeline to signal proper shutdown

    - Added sighup handler to try and shut down pytest and clean locks

    - Added file to track locks as a last ditch effort to free locks

    - Added --lock-timeout option to specify the timeout while waiting for meter locks

v0.8.3

    - Added peer-to-peer meter management (added --multi-db option)

    - Added expect script support (g5m.expect_shell())

v0.7
    - Added meter teardown support to parallel plugin

    - Added activate/deactivate mdb sub-commands

    - Added interactive shell (when 'mm sh' is used without args)
