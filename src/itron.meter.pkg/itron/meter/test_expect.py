# -*- coding: utf-8 -*-
import pytest

import itron.meter.Gen5Meter as G5
import logging
from itron.meter.expect import ParamikoExpect

@pytest.mark.needs_meter
def test_expect_flow(meter):
    with G5.SSHGen5Meter(meter, logger=logging.getLogger(), timeout=30) as meter:

        with ParamikoExpect(meter.connection.server.client, timeout=15) as exp:
            print(exp.login_match)
            logging.info("send hi there")
            exp.send_line("VAR='hi there'")
            exp.expect_prompt()

            exp.send_line('echo $VAR')
            print(str(exp.expect(['hi there'], timeout=4)))

            logging.info("Got 'hi there'")

            exp.expect_prompt()

            exp.send_line("echo 'double match example'")
            exp.expect("double match")
            exp.expect("double match")
            exp.expect_prompt()

            exp.send_line('echo "exit 123\n" > /mnt/common/fail.sh;chmod +x /mnt/common/fail.sh')
            exp.expect_prompt()
            exp.send_line("/mnt/common/fail.sh")
            exp.expect_prompt()
            exp.send_line('echo $?')
            match = exp.expect(r'^(\d+)[\r\n]$')
            code = int(match.match[1])
            assert code == 123

@pytest.mark.needs_meter
def test_expect_command(meter):
    with G5.SSHGen5Meter(meter, logger=logging.getLogger(), timeout=30) as meter:

        with ParamikoExpect(meter.connection.server.client, timeout=15) as exp:

            # now try out the command feature
            exp.command('echo "exit 223\n" > /mnt/common/fail2.sh;chmod +x /mnt/common/fail2.sh')
            code, output = exp.command("/mnt/common/fail2.sh")
            assert code == 223
            print("Output: ", output)

            exp.send_line("""cat <<EOF >/mnt/common/forever.sh
COUNTER=1
while true
do
    sleep 1
    date
    COUNTER=\\$(expr \\$COUNTER + 1)
    echo \\$COUNTER
done
EOF""") # note there is no <CR> on the last line, as send_line adds it
            exp.expect_prompt()
            exp.send_line("chmod +x /mnt/common/forever.sh")
            exp.expect_prompt()

            exp.send_line("/mnt/common/forever.sh")
            exp.expect("^5[\r\n]", timeout=10) # expect a five on a line by itself after five seconds
            exp.send_ctrl_c()
            exp.expect_prompt(timeout=10)
            exp.send_line('exit')
            exp.expect()