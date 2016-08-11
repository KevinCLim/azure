import asyncio
import sqlite3
import os
import urllib.parse
from asyncio.subprocess import PIPE
import db_helper


class SqliEmulator:
    def __init__(self, db_name, working_dir):
        self.db_name = db_name
        self.working_dir = working_dir
        self.helper = db_helper.DBHelper()
        self.query_map = None

    @asyncio.coroutine
    def setup_db(self):
        if self.query_map is None:
            self.query_map = yield from self.helper.create_query_map(self.working_dir, self.db_name)
        if not os.path.exists(self.working_dir):
            os.makedirs(self.working_dir)
        db = os.path.join(self.working_dir, self.db_name)
        if not os.path.exists(db):
            yield from self.helper.setup_db_from_config(self.working_dir, self.db_name)

    @asyncio.coroutine
    def check_sqli(self, path):
        @asyncio.coroutine
        def _run_cmd(cmd):
            proc = yield from asyncio.wait_for(asyncio.create_subprocess_exec(*cmd, stdout=PIPE), 5)
            line = yield from asyncio.wait_for(proc.stdout.readline(), 10)
            return line

        command = ['/usr/bin/python2', 'sqli_check.py', path]
        res = yield from _run_cmd(command)
        if res is not None:
            try:
                res = int(res.decode('utf-8'))
            except ValueError:
                res = 0
        return res

    @asyncio.coroutine
    def check_post_data(self, data):
        sqli_data = []
        for (param, value) in data['post_data'].items():
            sqli = yield from self.check_sqli(value)
            if sqli:
                sqli_data.append((param, value))
        return sqli_data

    @asyncio.coroutine
    def check_get_data(self, path):
        query = urllib.parse.urlparse(path).query
        parsed_queries = urllib.parse.parse_qsl(query)
        for q in parsed_queries:
            sqli = yield from self.check_sqli(q[1])
            return sqli

    @asyncio.coroutine
    def create_attacker_db(self, session):
        attacker_db_name = session.uuid.hex + '.db'
        attacker_db = yield from self.helper.copy_db(self.db_name, attacker_db_name, self.working_dir)
        session.associate_db(attacker_db)
        return attacker_db

    @staticmethod
    def prepare_get_query(path):
        path = urllib.parse.unquote(path)
        query = urllib.parse.urlparse(path).query
        parsed_query = urllib.parse.parse_qsl(query)
        return parsed_query

    @asyncio.coroutine
    def map_query(self, query):
        db_query = None
        param = query[0][0]
        param_value = query[0][1].replace('\'', ' ')
        tables = [k for k, v in self.query_map.items() if query[0][0] in v]
        if tables:
            db_query = 'SELECT * from ' + tables[0] + ' WHERE ' + param + '=' + param_value + ';'

        return db_query

    @staticmethod
    def execute_query(query, db):
        result = []
        conn = sqlite3.connect(db)
        c = conn.cursor()
        try:
            for row in c.execute(query):
                result.append(list(row))
        except sqlite3.OperationalError as e:
            result = str(e)
        return result

    @asyncio.coroutine
    def get_sqli_result(self, query, attacker_db):
        db_query = yield from self.map_query(query)
        if db_query is None:
            result = 'You have an error in your SQL syntax; check the manual\
                        that corresponds to your MySQL server version for the\
                        right syntax to use near {} at line 1'.format(query[0][0])
        else:
            execute_result = self.execute_query(db_query, attacker_db)
            if type(execute_result) == list:
                execute_result = ' '.join([str(x) for x in execute_result])
            result = dict(value=execute_result, page='/index.html')
        return result

    @asyncio.coroutine
    def handle(self, path, session, post_request=0):
        yield from self.setup_db()
        if not post_request:
            path = self.prepare_get_query(path)
        attacker_db = yield from self.create_attacker_db(session)
        result = yield from self.get_sqli_result(path, attacker_db)
        return result