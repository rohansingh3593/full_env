import os
from itron.meter.FwMan import Walker
from typing import List
from itron.meter.Walker import OWI_URL

""" handles finding and decoding agent releases from OWI_BUILDS repo """

class AgentInfo:
    pass

class AgentInfo:
    """ Agent information """
    def __init__(self, name: str, version: str, container_id: int, url: str, depends_on: List[AgentInfo]=[], has_daemon=True, reg_name = None):
        """ Represents an agent and associated information about the agent

        Some agents have daemon processes, while others are simply libraries.
        registration information is also sometimes inconsistent.

        @param name          The name of the agent.  Must match the daemon name with _Daemon at the end
        @param version       The version of the daemon
        @param container_id  Container that the agent lives in. this should be THIRD_PARTY_CONTAINER_ID or ITRON_CONTAINER_ID
        @param url           The web location in the itron network where the image to be installed on the meter lives
        @param depends_on    A list of library agents that are needed by the agent to function.
        @param has_daemon    Set to False if this is a library agent
        @param reg_name      The actual registration string the agent uses when registring with DI

        """
        self.url = url
        self.name = name
        self.version = version
        self.container_id = container_id
        self.depends_on = depends_on
        self.has_daemon = has_daemon
        if reg_name:
            self.reg_name = reg_name
        else:
            self.reg_name = name

    def __repr__(self) -> str:
        return f"AgentInfo({self.name},{self.container_id})"


THIRD_PARTY_CONTAINER_ID = '587464704'
ITRON_CONTAINER_ID = '50593792'

def get_name_from_url(url):
    items=url.split('/')
    name = None
    for x in range(len(items)):
        if items[x] == 'DI_Agents':
            name = items[x+1]
            break

    assert name, "URL does not have DI_Agents in path"
    items = os.path.basename(url).split('_')
    version = items[len(name.split("_"))]
    if not name.endswith("Agent"):
        name = name + "Agent"
    return name, version

class AgentInfoAuto(AgentInfo):
    def __init__(self, url, container_id=ITRON_CONTAINER_ID, depends_on=[]):
        name,version = get_name_from_url(url)
        super().__init__(name, version, container_id, url, depends_on)

class AgentMan:
    def get_versions(self, name):
        agentdir = f"{OWI_URL}DI_Agents/{name}"
        versions = Walker.glob(agentdir + '/*')
        return versions

    def get_rel_agent(self, name, rel_ver):
        versions = self.get_versions(name)
        version = versions[rel_ver]
        return self.get_agent(name, version)

    def get_agent(self, name, version):
        types = Walker.glob(version + '*')
        match = [match for match in types if 'DevInternal' in match]
        url = None
        if match:
            urls=Walker.glob(os.path.join(match[0], '*_TS.zip'))
            if len(urls) > 1:
                urls = [url for url in urls if os.path.basename(url).startswith(name) or os.path.basename(url).startswith('signed-')]
            url = urls[0]
        else:
            match = [match for match in types if 'Release' in match]
            url=Walker.glob(os.path.join(match[0], '*_TS.zip'))

        assert url, "don't know how to process agent download path"
        agentname,version = get_name_from_url(url)
        assert name == agentname

        #todo: extract container name from agent
        container = ITRON_CONTAINER_ID
        return AgentInfo(name, version, container, url)

