from kaizenbot.Misc.kbotdbconfig import DB_Dict as DB_Dict_default

# sadly, these should have been derived from base class of abstract db server
import io
import datetime
import logging
import abc
from rohan.meter.MeterInstance import MeterInstanceBase, MeterInstanceDB
from psycopg2 import sql
import psycopg2
import base64
from threading import RLock
import sqlite3
import os
import time


# make a copy of the base dictionary, and allow additions here
DB_Dict = DB_Dict_default.copy()

#
# since the pgre and psql db code is different
# create a db class that handles both the same way
#
class db(abc.ABC):

    @abc.abstractmethod
    def unlock_node(self, node):
        pass

    @abc.abstractmethod
    def lock_node(self, node):
        pass

    @abc.abstractmethod
    def exec(self, query):
        pass

    @abc.abstractmethod
    def read_nodes(self, query):
        pass

    @abc.abstractmethod
    def dump(self, file):
        pass

    def _verify_singularity(self, data):
        if not (data and
                isinstance(data, list) and
                len(data) == 1 and
                data[0] and
                isinstance(data[0], tuple) and
                len(data[0]) == 1):
            return False
        return True
    def close(self):
        if self.conn:
            self.conn.close()

    def __del__(self):
        self.close()

    def commit(self):
        if self.conn:
            self.conn.commit()

    def close(self):
        if self.conn:
            self.conn.close()

    def __del__(self):
        self.close()

    def commit(self):
        if self.conn:
            self.conn.commit()


class Cursor(object):
    def __init__(self, sqlite):
        self.cursor = sqlite.cursor()
    def __enter__(self):
        return self.cursor
    def __exit__(self, type, value, traceback):
        self.cursor.close()
class db_sql(db):
    def __init__(self, logger, source,platform=None, project=None, number_of_nodes=None, node_type=None, **kwargs):
        self.platform = platform
        self.project = project
        self.number_of_nodes = number_of_nodes
        self.node_type = node_type
        self.kwargs = kwargs
        self.conn = None
        self.logger = logger
        self.db_file = source
        try:
            self.conn = sqlite3.connect(self.db_file,uri=True, check_same_thread=False)
            self.conn.execute("PRAGMA foreign_keys = ON")
        except Exception as e:
            self.logger.error("Exception %s connecting to database", e)
            raise

        self.lock = RLock()
        self.shared_nodes = {}  # TYPE: DICT{str:int} {shared-node-ip:number of shared instances}
        self.shared_aps = {}    # TYPE: DICT{str:int} {shared-ap-ip:number of shared instances}
        self.plt_id = self.get_platform_id(self.platform, self.project)


    def get_platform_id(self, platform, project):
        query = '''SELECT platform_id
                   FROM platform
                   WHERE platform_name = '%s' and project_name = '%s'
                ''' % (platform, project)
        data = None
        with self.lock:
            with Cursor(self.conn) as curpg:
                curpg.execute(query)
                data = curpg.fetchall()

        if not data:
            return 'NULL'
        if not self._verify_singularity(data):
            return 'AMBIGUOUS'
        return data[0][0]


    def unlock_node(self, ip):
        query = '''UPDATE Node
                    SET node_busy = 'no'
                    WHERE node_ip = '%s' AND node_busy = 'yes'
                ''' % (str(ip))
        count = self.runquery_update(query)
        if count != 1:
            raise ValueError("Node not present in database")

    def lock_node(self, node):
        """
        attempt to lock a node for use.  retry for timeout seconds if busy
        """
        query = '''
            UPDATE {0} SET node_busy = 'yes',
                busy_change_count = busy_change_count + 1
                WHERE {1}'''.format('Node', f"node_busy = 'no' and node_ip = '{node}'")
        with self.lock:
            try:
                count = self.runquery_update(query)
                if count:
                    return True
                else:
                    return False
            except (Exception, psycopg2.Error) as error:
                self.logger.error("Error in locking meter: %s", error)
        return False


    def read_nodes(self, node_status='active', number_of_nodes=None, node_type=None, **kwargs):
        query = '''SELECT *
                    FROM Node
                    WHERE platform_id = '%s' and node_status = '%s'
                ''' % (self.plt_id,node_status)
        try:
            cursor = self.conn.cursor()
            cursor.execute(query)
            data = cursor.fetchall()
            result=[]
            columns_descr = cursor.description
            for item in data:
                line = {}
                for index in range(len(item)):
                    name = columns_descr[index][0]
                    line[name.upper()] = item[index]
                result.append(MeterInstanceDB(line,self))
        finally:
            cursor.close()

        return result

    def dump(self, file):
        result = file
        if file is None:
            result = io.StringIO
        result = io.StringIO()

        for line in self.conn.iterdump():
            result.write(line)
            result.write('\n')

        if file:
            return
        result.seek(0)
        return result.read()

    def exec(self, query):
        with Cursor(self.conn) as curpg:
            curpg.execute(query)
            self.conn.commit()
            if curpg.description:
                return curpg.fetchall()
        return None

    def runquery_update(self, query):
        try:
            with self.lock:
                with Cursor(self.conn) as curpg:
                    curpg.execute(query)
                    self.conn.commit()
                    return curpg.rowcount
        except Exception as e:
            self.logger.error('sqlite: Error %s',e)
            raise e

from contextlib import contextmanager
@contextmanager
def sql_connection(database, user, password, host, port):
    conn = None
    try:
        conn = psycopg2.connect(database=database, user=user, password=password,
                                         host=host, port=port)
        yield conn
    finally:
        if conn:
            conn.commit()
            conn.close()

class db_pgre(db):
    def __init__(self, logger, pgdb=None, pguser=None, pgpswd=None, pghost=None, pgport=None,
        schema=None,platform=None, project=None, number_of_nodes=None, node_type=None, **kwargs):
        self.schema = schema
        self.conn = None
        self.platform = platform
        self.project = project
        self.number_of_nodes = number_of_nodes
        self.node_type = node_type
        self.kwargs = kwargs
        self.logger = logger

        """ create a database connection to a SQLite database """
        self.pgdb = base64.b64decode(pgdb).decode()
        self.pguser = base64.b64decode(pguser).decode()
        self.pgpswd = base64.b64decode(pgpswd).decode()
        self.pghost = pghost
        self.pgport = pgport
        self.plt_id = self.get_platform_id(schema, platform, project)

    def get_platform_id(self, schema, platform, project):

        with sql_connection(database=self.pgdb, user=self.pguser, password=self.pgpswd,
                                         host=self.pghost, port=self.pgport) as conn:

            query = '''SELECT platform_id
                    FROM %s.platform
                    WHERE platform_name = '%s' and project_name = '%s'
                    ''' % (schema, platform, project)
            data = None
            with conn.cursor() as curpg:
                curpg.execute(query)
                data = curpg.fetchall()
            if not data:
                return 'NULL'
            if not self._verify_singularity(data):
                return 'AMBIGUOUS'
        return data[0][0]

    def runquery_update(self, query):
        try:
            with sql_connection(database=self.pgdb, user=self.pguser, password=self.pgpswd,
                                            host=self.pghost, port=self.pgport) as conn:

                with conn.cursor() as curpg:
                    curpg.execute(query)
                    conn.commit()
                    return curpg.rowcount
        except psycopg2.DatabaseError as e:
            self.logger.error('kbotdbserver_psql: Error %s', e)
            raise e


    def lock_node(self, node):
        """
        attempt to lock a node for use.  retry for timeout seconds if busy
        """
        if isinstance(node, MeterInstanceBase):
            node = node.ip_address

        where_condt = sql.SQL(f"node_busy = 'no' and node_ip = '{node}'")
        hostname = os.uname()[1]
        if os.getenv("BUILD_BUILDID"):
            hostname += "-" + os.getenv("BUILD_BUILDID")

        query = sql.SQL("UPDATE {tab} SET node_busy = 'yes', "
                        "busy_change_count = busy_change_count + 1, "
                        "last_busy_change = (SELECT now()), "
                        "lock_host = {host} "
                        "WHERE {condition}").format(tab=sql.SQL(self.schema + '.Node'), host=sql.Literal(hostname), condition=where_condt)
        try:
            count = self.runquery_update(query)
            if count:
                return True
            else:
                return False
        except (Exception, psycopg2.Error) as error:
            self.logger.error("Error in locking meter: %s", error)
        return False


    def exec(self, query):
        with sql_connection(database=self.pgdb, user=self.pguser, password=self.pgpswd,
                                         host=self.pghost, port=self.pgport) as conn:

            with conn.cursor() as curpg:
                curpg.execute(query)
                conn.commit()
                if curpg.description:
                    return curpg.fetchall()
        return None

    def unlock_node(self, ip):
        # TODO: could mark instance locked and use index to unlock instead of using addr
        if isinstance(ip,MeterInstanceBase):
            ip = ip.ip_address

        query = '''UPDATE %s.Node
                    SET node_busy = 'no', last_busy_change = (SELECT now())
                    WHERE node_ip = '%s' AND node_busy = 'yes'
                ''' % (self.schema, str(ip))
        retry = 10
        while retry:
            try:
                count = self.runquery_update(query)
                retry = 0
            except BaseException as e:
                retry -= 1
                if retry:
                    self.logger.warning("Exception %s connecting to database.  Retry %s more times", e, retry)
                    time.sleep(20)
                else:
                    self.logger.error("Final Exception %s, while unlocking node", e)
                    raise

        if count != 1:
            raise ValueError("Node not present in database")

    def read_nodes(self, node_status='active', number_of_nodes=None, node_type=None, **kwargs):
        if node_type:
            query = '''SELECT *
                       FROM %s.Node
                       WHERE platform_id = '%s' and node_device_type = %s and node_status LIKE '%s'
                    ''' % (self.schema, self.plt_id, node_type, node_status)
        else:
            query = '''SELECT *
                       FROM %s.Node
                       WHERE platform_id = '%s' and node_status LIKE '%s'
                    ''' % (self.schema, self.plt_id, node_status)

        with sql_connection(database=self.pgdb, user=self.pguser, password=self.pgpswd,
                                        host=self.pghost, port=self.pgport) as conn:

            with conn.cursor() as cursor:
                cursor.execute(query)
                data = cursor.fetchall()
                result=[]
                columns_descr = cursor.description
                for item in data:
                    line = {}
                    for index in range(len(item)):
                        name = columns_descr[index].name
                        line[name.upper()] = item[index]
                    result.append(MeterInstanceDB(line,self))
                del cursor

        return result

    def dump(self, file):
        query = """
SELECT *
FROM pg_catalog.pg_tables
WHERE schemaname != 'pg_catalog' AND
    schemaname != 'information_schema';
"""
        with sql_connection(database=self.pgdb, user=self.pguser, password=self.pgpswd,
                                        host=self.pghost, port=self.pgport) as conn:

            with conn.cursor() as cursor:
                cursor.execute(query)
                values = cursor.fetchall()
                result = file
                if file is None:
                    result = io.StringIO
                result = io.StringIO()
                for table in values:
                    table_name=table[0] + "." + table[1]
                    if table_name:
                        cursor.execute("SELECT * FROM %s" % (table_name))
                        column_names = []
                        columns_descr = cursor.description
                        for c in columns_descr:
                            column_names.append(c[0])
                        insert_prefix = 'INSERT INTO %s (%s) VALUES ' % (table_name, ', '.join(column_names))
                        rows = cursor.fetchall()
                        for row in rows:
                            row_data = []
                            for rd in row:
                                if rd is None:
                                    row_data.append('NULL')
                                elif isinstance(rd, datetime.datetime):
                                    row_data.append("'%s'" % (rd.strftime('%Y-%m-%d %H:%M:%S') ))
                                else:
                                    row_data.append(repr(rd))
                            result.write('%s (%s);\n\n' % (insert_prefix, ', '.join(row_data)))  # this is the text that will be put in the SQL file
                if file:
                    return
                result.seek(0)
                return result.read()

    def deactivate_node(self, ip):
        query = '''UPDATE %s.Node
                    SET node_status = 'inactive'
                    WHERE node_ip = '%s'
                ''' % (self.schema, str(ip))
        count = self.runquery_update(query)
        if count != 1:
            raise ValueError("Node not present in database")

    def activate_node(self, ip):
        query = '''UPDATE %s.Node
                    SET node_status = 'active'
                    WHERE node_ip = '%s'
                ''' % (self.schema, str(ip))
        count = self.runquery_update(query)
        if count != 1:
            raise ValueError("Node not present in database")



class MeterDBBase():
    def __init__(self, options, logger=logging.getLogger()):
        self.lock = RLock()
        self.lock_result=[]
        self.logger = logger
        self.options = options

    def close(self):
        with self.lock:
            self.unlock_all()
        super().close()

    def __del__(self):
        self.close()

    def known_dbs(self):
        return DB_Dict.keys()

    def deactivate(self, node):
        super().deactivate_node(str(node))

    def activate(self, node):
        super().activate_node(str(node))

    def unlock_all(self):
        with self.lock:
            for x in self.lock_result:
                self.logger.warning("Unlocking node %s", x)
                super().unlock_node(x)
            self.lock_result = []

    def free_nodes(self, nodes):
        with self.lock:
            for x in nodes:
                super().unlock_node(x)

    def unlock_node(self, node):
        with self.lock:
            for idx in range(len(self.lock_result)):
                x = self.lock_result[idx]
                if str(x) == str(node):
                    super().unlock_node(str(node))
                    del self.lock_result[idx]
                    return
            logging.warning("Tried to unlock %s there is no record of in the active context,  maybe it was already unlocked", str(node))

    def force_unlock_node(self, node):
        with self.lock:
            for idx in range(len(self.lock_result)):
                x = self.lock_result[idx]
                if str(x) == str(node):
                    super().unlock_node(str(node))
                    del self.lock_result[idx]
                    return

            super().unlock_node(str(node))

    def get_meters(self, node_status='active'):
        return super().read_nodes(node_status)

    def lock_node(self, node_instance, track=True):
        with self.lock:
            ok = super().lock_node(node_instance)
            if ok and track:
                self.lock_result.append(node_instance)
            return ok


    def get_dbinfo(self):
        return DB_Dict

    def execute_sql(self, query):
        return super().exec(query)

    def dump(self, file=None):
        return super().dump(file)

class MeterDBpgre(MeterDBBase, db_pgre):
    def __init__(self, options, logger, *args, **kwargs):
        db_pgre.__init__(self, logger, *args, **kwargs)
        MeterDBBase.__init__(self, options, logger)

class MeterDBsql(MeterDBBase, db_sql):
    def __init__(self, options, logger, *args, **kwargs):
        db_sql.__init__(self, logger, *args, **kwargs)
        MeterDBBase.__init__(self, options, logger)

class MeterDB():
    def __new__(self, options, logger=logging.getLogger()):
        self.lock = RLock()
        self.lock_result=[]
        self.db = None
        self.logger = logger
        options = options.split(',')

        # first item is server or file
        source = options.pop(0)
        # the rest of the options are args to aquire_platform in the form arg=value
        # so convert to dict
        filters={}
        for x in options:
            item = x.split('=')
            assert len(item) == 2, "DUT database specification must have name=value format, missing equal sign"
            filters[item[0]] = item[1]

        if source in DB_Dict:
            assert('schema') in filters, "for postgresql db, schema={schema} must be specified in --dut-db option. Use schema=? for a list"
            schema = filters['schema']
            self.schema = schema
            x = DB_Dict[source]
            self.dbtype = x['db_type']
            if x['db_type'] == 'psql':
                assert schema in x, f"Schema must be one of {x.keys()}"

                self.db_name = x[schema]['db_name']
                self.db_user = x[schema]['db_user']
                self.db_pwd = x[schema]['db_pwd']
                self.URI = source
                return MeterDBpgre(options,logger,self.db_name,self.db_user,self.db_pwd,self.URI.split(':')[0],self.URI.split(':')[1], **filters)
            else:
                return MeterDBsql(source, **filters)
        else:
            if source.startswith('file:'):
                return MeterDBsql(source[5:], **filters)
            else:
                src=source.split(':')
                filters['pghost'] = src[0]
                filters['pgport'] = src[1]

                return MeterDBpgre(options, logger, **filters)

        assert False # logic error if we get here