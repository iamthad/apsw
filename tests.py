#!/usr/bin/env python

# APSW test suite

import apsw

print "Testing with APSW file",apsw.__file__
print "          APSW version",apsw.apswversion()
print "    SQLite lib version",apsw.sqlitelibversion()
print "SQLite headers version",apsw.SQLITE_VERSION_NUMBER

if [int(x) for x in apsw.sqlitelibversion().split(".")]<[3,5,2]:
    print "You are using an earlier version of SQLite than recommended"

import sys
sys.stdout.flush()

# unittest stuff from here on

import unittest
import os
import math
import random
import time
import threading
import Queue
import traceback
import StringIO
import gc

# helper functions
def randomintegers(howmany):
    for i in xrange(howmany):
        yield (random.randint(0,9999999999),)

# An instance of this class is used to get the -1 return value to the
# C api PyObject_IsTrue
class BadIsTrue(int):
    def __nonzero__(self):
        1/0

# helper class - runs code in a seperate thread
class ThreadRunner(threading.Thread):

    def __init__(self, callable, *args, **kwargs):
        threading.Thread.__init__(self)
        self.setDaemon(True)
        self.callable=callable
        self.args=args
        self.kwargs=kwargs
        self.q=Queue.Queue()
        self.started=False

    def start(self):
        if not self.started:
            self.started=True
            threading.Thread.start(self)

    def go(self):
        self.start()
        t,res=self.q.get()
        if t: # result
            return res
        else: # exception
            raise res[0], res[1], res[2]

    def run(self):
        try:
            self.q.put( (True, self.callable(*self.args, **self.kwargs)) )
        except:
            self.q.put( (False, sys.exc_info()) )


# main test class/code
class APSW(unittest.TestCase):


    connection_nargs={ # number of args for function.  those not listed take zero
        'createaggregatefunction': 2,
        'complete': 1,
        'createcollation': 2,
        'createscalarfunction': 2,
        'setauthorizer': 1,
        'setbusyhandler': 1,
        'setbusytimeout': 1,
        'setcommithook': 1,
        'setprofile': 1,
        'setrollbackhook': 1,
        'setupdatehook': 1,
        'setprogresshandler': 2,
        'enableloadextension': 1,
        'createmodule': 2,
        }

    cursor_nargs={
        'execute': 1,
        'executemany': 2,
        'setexectrace': 1,
        'setrowtrace': 1,
        }

    blob_nargs={
        'write': 1,
        'read': 1,
        'seek': 2
        }

    
    def setUp(self, dbname="testdb"):
        # clean out database and journal from last run
        for i in "-journal", "":
            if os.path.exists(dbname+i):
                os.remove(dbname+i)
            assert not os.path.exists(dbname+i)
        self.db=apsw.Connection(dbname)

    def tearDown(self):
        # we don't delete the database file itself.  it will be
        # left around if there was a failure
        self.db.close(True)
        del self.db
        apsw.connection_hooks=[] # back to default value

    def assertTableExists(self, tablename):
        self.failUnlessEqual(self.db.cursor().execute("select count(*) from ["+tablename+"]").next()[0], 0)

    def assertTableNotExists(self, tablename):
        # you get SQLError if the table doesn't exist!
        self.assertRaises(apsw.SQLError, self.db.cursor().execute, "select count(*) from ["+tablename+"]")

    def testSanity(self):
        "Check all parts compiled and are present"
        # check some error codes etc are present - picked first middle and last from lists in code
        apsw.SQLError
        apsw.MisuseError
        apsw.NotADBError
        apsw.ThreadingViolationError
        apsw.BindingsError
        apsw.ExecTraceAbort

    def testConnection(self):
        "Test connection opening"
        # bad keyword arg
        self.assertRaises(TypeError, apsw.Connection, ":memory:", user="nobody")
        # wrong types
        self.assertRaises(TypeError, apsw.Connection, 3)
        # non-unicode
        self.assertRaises(UnicodeDecodeError, apsw.Connection, "\xef\x22\xd3\x9e")
        # bad file (cwd)
        self.assertRaises(apsw.CantOpenError, apsw.Connection, ".")
        # bad open flags can't be tested as sqlite accepts them all - ticket #3037
        # self.assertRaises(apsw.CantOpenError, apsw.Connection, "<non-existent-file>", flags=65535)

        # bad vfs
        self.assertRaises(TypeError, apsw.Connection, "foo", vfs=3, flags=-1)
        self.assertRaises(apsw.SQLError, apsw.Connection, "foo", vfs="jhjkds", flags=-1)

    def testMemoryLeaks(self):
        "MemoryLeaks: Run with a memory profiler such as valgrind and debug Python"
        # make and toss away a bunch of db objects, cursors, functions etc - if you use memory profiling then
        # simple memory leaks will show up
        c=self.db.cursor()
        c.execute("create table foo(x)")
        c.executemany("insert into foo values(?)", ( [1], [None], [math.pi], ["jkhfkjshdf"], [u"\u1234\u345432432423423kjgjklhdfgkjhsdfjkghdfjskh"],
                                                     [buffer("78696ghgjhgjhkgjkhgjhg\xfe\xdf")]))
        for i in xrange(MEMLEAKITERATIONS):
            db=apsw.Connection("testdb")
            db.createaggregatefunction("aggfunc", lambda x: x)
            db.createscalarfunction("scalarfunc", lambda x: x)
            db.setbusyhandler(lambda x: False)
            db.setbusytimeout(1000)
            db.setcommithook(lambda x=1: 0)
            db.setrollbackhook(lambda x=2: 1)
            db.setupdatehook(lambda x=3: 2)
            for i in xrange(100):
                c2=db.cursor()
                c2.setrowtrace(lambda x: (x,))
                c2.setexectrace(lambda x,y: True)
                for row in c2.execute("select * from foo"+" "*i):  # spaces on end defeat statement cache
                    pass
            del c2
            db.close()
            del db

    def testBindings(self):
        "Check bindings work correctly"
        c=self.db.cursor()
        c.execute("create table foo(x,y,z)")
        vals=(
            ("(?,?,?)", (1,2,3)),
            ("(?,?,?)", [1,2,3]),
            ("(?,?,?)", range(1,4)),
            ("(:a,$b,:c)", {'a': 1, 'b': 2, 'c': 3}),
            ("(1,?,3)", (2,)),
            ("(1,$a,$c)", {'a': 2, 'b': 99, 'c': 3}),
            # some unicode fun
            (u"($\N{LATIN SMALL LETTER E WITH CIRCUMFLEX},:\N{LATIN SMALL LETTER A WITH TILDE},$\N{LATIN SMALL LETTER O WITH DIAERESIS})", (1,2,3)),
            (u"($\N{LATIN SMALL LETTER E WITH CIRCUMFLEX},:\N{LATIN SMALL LETTER A WITH TILDE},$\N{LATIN SMALL LETTER O WITH DIAERESIS})",
             {u"\N{LATIN SMALL LETTER E WITH CIRCUMFLEX}": 1,
              u"\N{LATIN SMALL LETTER A WITH TILDE}": 2,
              u"\N{LATIN SMALL LETTER O WITH DIAERESIS}": 3,
              }),
              
            )
        for str,bindings in vals:
            c.execute("insert into foo values"+str, bindings)
            self.failUnlessEqual(c.execute("select * from foo").next(), (1,2,3))
            c.execute("delete from foo")
            
        # currently missing dict keys come out as null
        c.execute("insert into foo values(:a,:b,$c)", {'a': 1, 'c':3}) # 'b' deliberately missing
        self.failUnlessEqual((1,None,3), c.execute("select * from foo").next())
        c.execute("delete from foo")

        # these ones should cause errors
        vals=(
            (apsw.BindingsError, "(?,?,?)", (1,2)), # too few
            (apsw.BindingsError, "(?,?,?)", (1,2,3,4)), # too many
            (TypeError,          "(?,?,?)", None), # none at all
            (apsw.BindingsError, "(?,?,?)", {'a': 1}), # ? type, dict bindings (note that the reverse will work since all
                                                       # named bindings are also implicitly numbered
            (TypeError,          "(?,?,?)", 2),    # not a dict or sequence
            (TypeError,          "(:a,:b,:c)", {'a': 1, 'b': 2, 'c': self}), # bad type for c
            )
        for exc,str,bindings in vals:
            self.assertRaises(exc, c.execute, "insert into foo values"+str, bindings)

        # with multiple statements
        c.execute("insert into foo values(?,?,?); insert into foo values(?,?,?)", (99,100,101,102,103,104))
        self.assertRaises(apsw.BindingsError, c.execute, "insert into foo values(?,?,?); insert into foo values(?,?,?); insert some more",
                          (100,100,101,1000,103)) # too few
        self.assertRaises(apsw.BindingsError, c.execute, "insert into foo values(?,?,?); insert into foo values(?,?,?)",
                          (101,100,101,1000,103,104,105)) # too many
        # check the relevant statements did or didn't execute as appropriate
        self.failUnlessEqual(self.db.cursor().execute("select count(*) from foo where x=99").next()[0], 1)
        self.failUnlessEqual(self.db.cursor().execute("select count(*) from foo where x=102").next()[0], 1)
        self.failUnlessEqual(self.db.cursor().execute("select count(*) from foo where x=100").next()[0], 1)
        self.failUnlessEqual(self.db.cursor().execute("select count(*) from foo where x=1000").next()[0], 0)
        self.failUnlessEqual(self.db.cursor().execute("select count(*) from foo where x=101").next()[0], 1)
        self.failUnlessEqual(self.db.cursor().execute("select count(*) from foo where x=105").next()[0], 0)

        # check there are some bindings!
        self.assertRaises(apsw.BindingsError, c.execute, "create table bar(x,y,z);insert into bar values(?,?,?)")

        # across executemany
        vals=( (1,2,3), (4,5,6), (7,8,9) )
        c.executemany("insert into foo values(?,?,?);", vals)
        for x,y,z in vals:
            self.failUnlessEqual(c.execute("select * from foo where x=?",(x,)).next(), (x,y,z))

        # with an iterator
        def myvals():
            for i in range(10):
                yield {'a': i, 'b': i*10, 'c': i*100}
        c.execute("delete from foo")
        c.executemany("insert into foo values($a,:b,$c)", myvals())
        c.execute("delete from foo")

        # errors for executemany
        self.assertRaises(TypeError, c.executemany, "statement", 12, 34, 56) # incorrect num params
        self.assertRaises(TypeError, c.executemany, "statement", 12) # wrong type
        self.assertRaises(apsw.SQLError, c.executemany, "syntax error", [(1,)]) # error in prepare
        def myiter():
            yield 1/0
        self.assertRaises(ZeroDivisionError, c.executemany, "statement", myiter()) # immediate error in iterator
        def myiter():
            yield self
        self.assertRaises(TypeError, c.executemany, "statement", myiter()) # immediate bad type
        self.assertRaises(TypeError, c.executemany, "select ?", ((self,), (1))) # bad val
        c.executemany("statement", ()) # empty sequence

        # error in iterator after a while
        def myvals():
            for i in range(2):
                yield {'a': i, 'b': i*10, 'c': i*100}
            1/0
        self.assertRaises(ZeroDivisionError, c.executemany, "insert into foo values($a,:b,$c)", myvals())
        self.failUnlessEqual(c.execute("select count(*) from foo").next()[0], 2)
        c.execute("delete from foo")

        # return bad type from iterator after a while
        def myvals():
            for i in range(2):
                yield {'a': i, 'b': i*10, 'c': i*100}
            yield self

        self.assertRaises(TypeError, c.executemany, "insert into foo values($a,:b,$c)", myvals())
        self.failUnlessEqual(c.execute("select count(*) from foo").next()[0], 2)
        c.execute("delete from foo")

        # some errors in executemany
        self.assertRaises(apsw.BindingsError, c.executemany, "insert into foo values(?,?,?)", ( (1,2,3), (1,2,3,4)))
        self.assertRaises(apsw.BindingsError, c.executemany, "insert into foo values(?,?,?)", ( (1,2,3), (1,2)))

        # incomplete execution across executemany
        c.executemany("select * from foo; select ?", ( (1,), (2,) )) # we don't read
        self.assertRaises(apsw.IncompleteExecutionError, c.executemany, "begin")

        # set type (pysqlite error with this)
        if sys.version_info>=(2, 4, 0):
            c.execute("create table xxset(x,y,z)")
            c.execute("insert into xxset values(?,?,?)", set((1,2,3)))
            c.executemany("insert into xxset values(?,?,?)", (set((4,5,6)),))
            result=[(1,2,3), (4,5,6)]
            for i,v in enumerate(c.execute("select * from xxset order by x")):
                self.failUnlessEqual(v, result[i])

    def testCursor(self):
        "Check functionality of the cursor"
        c=self.db.cursor()
        # shouldn't be able to manually create
        self.assertRaises(TypeError, type(c))
        
        # give bad params
        self.assertRaises(TypeError, c.execute)
        self.assertRaises(TypeError, "foo", "bar", "bam")

        # empty statements
        c.execute("")
        c.execute(" ;\n\t\r;;")
        
        # unicode
        self.failUnlessEqual(3, c.execute(u"select 3").next()[0])
        self.assertRaises(UnicodeDecodeError, c.execute, "\x99\xaa\xbb\xcc")
        
        # does it work?
        c.execute("create table foo(x,y,z)")
        # table should be empty
        entry=-1
        for entry,values in enumerate(c.execute("select * from foo")):
            pass
        self.failUnlessEqual(entry,-1, "No rows should have been returned")
        # add ten rows
        for i in range(10):
            c.execute("insert into foo values(1,2,3)")
        for entry,values in enumerate(c.execute("select * from foo")):
            # check we get back out what we put in
            self.failUnlessEqual(values, (1,2,3))
        self.failUnlessEqual(entry, 9, "There should have been ten rows")
        # does getconnection return the right object
        self.failUnless(c.getconnection() is self.db)
        # check getdescription - note column with space in name and [] syntax to quote it
        cols=(
            ("x a space", "integer"),
            ("y", "text"),
            ("z", "foo"),
            ("a", "char"),
            (u"\N{LATIN SMALL LETTER E WITH CIRCUMFLEX}\N{LATIN SMALL LETTER A WITH TILDE}", u"\N{LATIN SMALL LETTER O WITH DIAERESIS}\N{LATIN SMALL LETTER U WITH CIRCUMFLEX}"),
            )
        c.execute("drop table foo; create table foo (%s)" % (", ".join(["[%s] %s" % (n,t) for n,t in cols]),))
        c.execute("insert into foo([x a space]) values(1)")
        for row in c.execute("select * from foo"):
            self.failUnlessEqual(cols, c.getdescription())
        # execution is complete ...
        self.assertRaises(apsw.ExecutionCompleteError, c.getdescription)
        self.assertRaises(StopIteration, c.next)
        self.assertRaises(StopIteration, c.next)
        # nulls for getdescription
        for row in c.execute("pragma user_version"):
            self.assertEqual(c.getdescription(), ( ('user_version', None), ))
        # incomplete
        c.execute("select * from foo; create table bar(x)") # we don't bother reading leaving 
        self.assertRaises(apsw.IncompleteExecutionError, c.execute, "select * from foo") # execution incomplete
        self.assertTableNotExists("bar")
        # autocommit
        self.assertEqual(True, self.db.getautocommit())
        c.execute("begin immediate")
        self.assertEqual(False, self.db.getautocommit())
        # pragma
        c.execute("pragma user_version")
        c.execute("pragma pure=nonsense")
        # error
        self.assertRaises(apsw.SQLError, c.execute, "create table bar(x,y,z); this is a syntax error; create table bam(x,y,z)")
        self.assertTableExists("bar")
        self.assertTableNotExists("bam")

    def testTypes(self):
        "Check type information is maintained"
        c=self.db.cursor()
        c.execute("create table foo(row,x)")
        vals=("a simple string",  # "ascii" string
              "0123456789"*200000, # a longer string
              u"a \u1234 unicode \ufe54 string \u0089",  # simple unicode string
              u"\N{BLACK STAR} \N{WHITE STAR} \N{LIGHTNING} \N{COMET} ", # funky unicode
              97, # integer
              2147483647,   # numbers on 31 bit boundary (32nd bit used for integer sign), and then
              -2147483647,  # start using 32nd bit (must be represented by 64bit to avoid losing
              2147483648L,  # detail)
              -2147483648L,
              2147483999L,
              -2147483999L,
              sys.maxint,
              992147483999L,
              -992147483999L,
              9223372036854775807L,
              -9223372036854775808L,
              buffer("a set of bytes"),      # bag of bytes initialised from a string, but don't confuse it with a
              buffer("".join([chr(x) for x in range(256)])), # string
              buffer("".join([chr(x) for x in range(256)])*20000),  # non-trivial size
              None,  # our good friend NULL/None
              1.1,  # floating point can't be compared exactly - failUnlessAlmostEqual is used to check
              10.2, # see Appendix B in the Python Tutorial 
              1.3,
              1.45897589347E97,
              5.987987/8.7678678687676786,
              math.pi,
              True,  # derived from integer
              False
              )
        for i,v in enumerate(vals):
            c.execute("insert into foo values(?,?)", (i, v))

        # add function to test conversion back as well
        def snap(*args):
            return args[0]
        self.db.createscalarfunction("snap", snap)

        # now see what we got out
        count=0
        for row,v,fv in c.execute("select row,x,snap(x) from foo"):
            count+=1
            if type(vals[row]) is float:
                self.failUnlessAlmostEqual(vals[row], v)
                self.failUnlessAlmostEqual(vals[row], fv)
            else:
                self.failUnlessEqual(vals[row], v)
                self.failUnlessEqual(vals[row], fv)
        self.failUnlessEqual(count, len(vals))

        # check some out of bounds conditions
        # integer greater than signed 64 quantity (SQLite only supports up to that)
        self.assertRaises(OverflowError, c.execute, "insert into foo values(9999,?)", (922337203685477580799L,))
        self.assertRaises(OverflowError, c.execute, "insert into foo values(9999,?)", (-922337203685477580799L,))

        # invalid character data - non-ascii data must be provided in unicode
        self.assertRaises(UnicodeDecodeError, c.execute, "insert into foo values(9999,?)", ("\xfe\xfb\x80\x92",))

        # not valid types for SQLite
        self.assertRaises(TypeError, c.execute, "insert into foo values(9999,?)", (apsw,)) # a module
        self.assertRaises(TypeError, c.execute, "insert into foo values(9999,?)", (type,)) # type
        self.assertRaises(TypeError, c.execute, "insert into foo values(9999,?)", (dir,))  # function

        # check nothing got inserted
        self.failUnlessEqual(0, c.execute("select count(*) from foo where row=9999").next()[0])

        # playing with default encoding and non-ascii strings
        enc=sys.getdefaultencoding()
        reload(sys) # gets setdefaultencoding function back
        try:
            for v in vals:
                if type(v)!=unicode:
                    continue
                def encoding(*args):
                    return v.encode("utf8") # returns as str not unicode
                self.db.createscalarfunction("encoding", encoding)
                sys.setdefaultencoding("utf8")
                for row in c.execute("select encoding(3)"):
                    self.failUnlessEqual(v, row[0])
                c.execute("insert into foo values(1234,?)", (v.encode("utf8"),))
                for row in c.execute("select x from foo where rowid="+`self.db.last_insert_rowid()`):
                    self.failUnlessEqual(v, row[0])
        finally:
            sys.setdefaultencoding(enc)
        
    def testAuthorizer(self):
        "Verify the authorizer works"
        def authorizer(operation, paramone, paramtwo, databasename, triggerorview):
            # we fail creates of tables starting with "private"
            if operation==apsw.SQLITE_CREATE_TABLE and paramone.startswith("private"):
                return apsw.SQLITE_DENY
            return apsw.SQLITE_OK
        c=self.db.cursor()
        # this should succeed
        c.execute("create table privateone(x)")
        # this should fail
        self.assertRaises(TypeError, self.db.setauthorizer, 12) # must be callable
        self.db.setauthorizer(authorizer)
        self.assertRaises(apsw.AuthError, c.execute, "create table privatetwo(x)")
        # this should succeed
        self.db.setauthorizer(None)
        c.execute("create table privatethree(x)")

        self.assertTableExists("privateone")
        self.assertTableNotExists("privatetwo")
        self.assertTableExists("privatethree")

        # error in callback
        def authorizer(operation, *args):
            if operation==apsw.SQLITE_CREATE_TABLE:
                1/0
            return apsw.SQLITE_OK
        self.db.setauthorizer(authorizer)
        self.assertRaises(ZeroDivisionError, c.execute, "create table shouldfail(x)")
        self.assertTableNotExists("shouldfail")

        # bad return type in callback
        def authorizer(operation, *args):
            return "a silly string"
        self.db.setauthorizer(authorizer)
        self.assertRaises(TypeError, c.execute, "create table shouldfail(x); select 3+5")
        self.db.setauthorizer(None) # otherwise next line will fail!
        self.assertTableNotExists("shouldfail")

        # back to normal
        self.db.setauthorizer(None)
        c.execute("create table shouldsucceed(x)")
        self.assertTableExists("shouldsucceed")

    def testExecTracing(self):
        "Verify tracing of executed statements and bindings"
        c=self.db.cursor()
        cmds=[] # this is maniulated in tracefunc
        def tracefunc(cmd, bindings):
            cmds.append( (cmd, bindings) )
            return True
        c.execute("create table one(x,y,z)")
        self.failUnlessEqual(len(cmds),0)
        self.assertRaises(TypeError, c.setexectrace, 12) # must be callable
        c.setexectrace(tracefunc)
        statements=[
            ("insert into one values(?,?,?)", (1,2,3)),
            ("insert into one values(:a,$b,$c)", {'a': 1, 'b': "string", 'c': None}),
            ]
        for cmd,values in statements:
            c.execute(cmd, values)
        self.failUnlessEqual(cmds, statements)
        self.failUnless(c.getexectrace() is tracefunc)
        c.setexectrace(None)
        self.failUnless(c.getexectrace() is None)
        c.execute("create table bar(x,y,z)")
        # cmds should be unchanged
        self.failUnlessEqual(cmds, statements)
        # tracefunc can abort execution
        count=c.execute("select count(*) from one").next()[0]
        def tracefunc(cmd, bindings):
            return False # abort
        c.setexectrace(tracefunc)
        self.assertRaises(apsw.ExecTraceAbort, c.execute, "insert into one values(1,2,3)")
        # table should not have been modified
        c.setexectrace(None)
        self.failUnlessEqual(count, c.execute("select count(*) from one").next()[0])
        # error in tracefunc
        def tracefunc(cmd, bindings):
            1/0
        c.setexectrace(tracefunc)
        self.assertRaises(ZeroDivisionError, c.execute, "insert into one values(1,2,3)")
        c.setexectrace(None)
        self.failUnlessEqual(count, c.execute("select count(*) from one").next()[0])
        # test across executemany and multiple statments
        counter=[0]
        def tracefunc(cmd, bindings):
            counter[0]=counter[0]+1
            return True
        c.setexectrace(tracefunc)
        c.execute("create table two(x);insert into two values(1); insert into two values(2); insert into two values(?); insert into two values(?)",
                  (3, 4))
        self.failUnlessEqual(counter[0], 5)
        counter[0]=0
        c.executemany("insert into two values(?); insert into two values(?)", [[n,n+1] for n in range(5)])
        self.failUnlessEqual(counter[0], 10)
        # error in func but only after a while
        c.execute("delete from two")
        counter[0]=0
        def tracefunc(cmd, bindings):
            counter[0]=counter[0]+1
            if counter[0]>3:
                1/0
            return True
        c.setexectrace(tracefunc)
        self.assertRaises(ZeroDivisionError, c.execute,
                          "insert into two values(1); insert into two values(2); insert into two values(?); insert into two values(?)",
                          (3, 4))
        self.failUnlessEqual(counter[0], 4)
        c.setexectrace(None)
        # check the first statements got executed
        self.failUnlessEqual(3, c.execute("select max(x) from two").next()[0])
        # executemany
        def tracefunc(cmd, bindings):
            1/0
        c.setexectrace(tracefunc)
        self.assertRaises(ZeroDivisionError, c.executemany, "select ?", [(1,)])
        c.setexectrace(None)
        # tracefunc with wrong number of arguments
        def tracefunc(a,b,c,d,e,f):
            1/0
        c.setexectrace(tracefunc)
        self.assertRaises(TypeError, c.execute, "select max(x) from two")
        def tracefunc(*args):
            return BadIsTrue()
        c.setexectrace(tracefunc)
        self.assertRaises(ZeroDivisionError, c.execute, "select max(x) from two")

    def testRowTracing(self):
        "Verify row tracing"
        c=self.db.cursor()
        c.execute("create table foo(x,y,z)")
        vals=(1,2,3)
        c.execute("insert into foo values(?,?,?)", vals)
        def tracefunc(*result):
            return tuple([7 for i in result])
        # should get original row back
        self.failUnlessEqual(c.execute("select * from foo").next(), vals)
        self.assertRaises(TypeError, c.setrowtrace, 12) # must be callable
        c.setrowtrace(tracefunc)
        self.failUnless(c.getrowtrace() is tracefunc)
        # all values replaced with 7
        self.failUnlessEqual(c.execute("select * from foo").next(), tuple([7]*len(vals)))
        def tracefunc(*result):
            return (7,)
        # a single 7
        c.setrowtrace(tracefunc)
        self.failUnlessEqual(c.execute("select * from foo").next(), (7,))
        # no alteration again
        c.setrowtrace(None)
        self.failUnlessEqual(c.execute("select * from foo").next(), vals)
        # error in function
        def tracefunc(*result):
            1/0
        c.setrowtrace(tracefunc)
        try:
            for row in c.execute("select * from foo"):
                self.fail("Should have had exception")
                break
        except ZeroDivisionError:
            pass
        c.setrowtrace(None)
        self.failUnlessEqual(c.execute("select * from foo").next(), vals)
        # returning null
        c.execute("create table bar(x)")
        c.executemany("insert into bar values(?)", [[x] for x in range(10)])
        counter=[0]
        def tracefunc(*args):
            counter[0]=counter[0]+1
            if counter[0]%2:
                return None
            return args
        c.setrowtrace(tracefunc)
        countertoo=0
        for row in c.execute("select * from bar"):
            countertoo+=1
        c.setrowtrace(None)
        self.failUnlessEqual(countertoo, 5) # half the rows should be skipped

    def testScalarFunctions(self):
        "Verify scalar functions"
        c=self.db.cursor()
        def ilove7(*args):
            return 7
        self.assertRaises(TypeError, self.db.createscalarfunction, "twelve", 12) # must be callable
        self.assertRaises(TypeError, self.db.createscalarfunction, "twelve", 12, 27, 28) # too many params
        self.assertRaises(apsw.SQLError, self.db.createscalarfunction, "twelve", ilove7, 900) # too many args
        self.assertRaises(TypeError, self.db.createscalarfunction, u"twelve\N{BLACK STAR}", ilove7) # must be ascii
        self.db.createscalarfunction("seven", ilove7)
        c.execute("create table foo(x,y,z)")
        for i in range(10):
            c.execute("insert into foo values(?,?,?)", (i,i,i))
        for i in range(10):
            self.failUnlessEqual( (7,), c.execute("select seven(x,y,z) from foo where x=?", (i,)).next())
        # clear func
        self.assertRaises(apsw.BusyError, self.db.createscalarfunction,"seven", None) # active select above so no funcs can be changed
        for row in c.execute("select null"): pass # no active sql now
        self.db.createscalarfunction("seven", None) 
        # function names are limited to 255 characters - SQLerror is the rather unintuitive error return
        self.assertRaises(apsw.SQLError, self.db.createscalarfunction, "a"*300, ilove7)
        # have an error in a function
        def badfunc(*args):
            return 1/0
        self.db.createscalarfunction("badscalarfunc", badfunc)
        self.assertRaises(ZeroDivisionError, c.execute, "select badscalarfunc(*) from foo")
        # return non-allowed types
        for v in ({'a': 'dict'}, ['a', 'list'], self):
            def badtype(*args):
                return v
            self.db.createscalarfunction("badtype", badtype)
            self.assertRaises(TypeError, c.execute, "select badtype(*) from foo")
        # return non-unicode string
        def ilove8bit(*args):
            return "\x99\xaa\xbb\xcc"
        
        self.db.createscalarfunction("ilove8bit", ilove8bit)
        self.assertRaises(UnicodeDecodeError, c.execute, "select ilove8bit(*) from foo")
        # coverage
        def bad(*args): 1/0
        self.db.createscalarfunction("bad", bad)
        self.assertRaises(ZeroDivisionError, c.execute, "select bad(3)+bad(4)")

    def testAggregateFunctions(self):
        "Verify aggregate functions"
        c=self.db.cursor()
        c.execute("create table foo(x,y,z)")
        # aggregate function
        class longest:
            def __init__(self):
                self.result=""
                
            def step(self, context, *args):
                for i in args:
                    if len(str(i))>len(self.result):
                        self.result=str(i)

            def final(self, context):
                return self.result

            def factory():
                v=longest()
                return None,v.step,v.final
            factory=staticmethod(factory)

        self.assertRaises(TypeError, self.db.createaggregatefunction,True, True, True, True) # wrong number/type of params
        self.assertRaises(TypeError, self.db.createaggregatefunction,"twelve", 12) # must be callable
        self.assertRaises(apsw.SQLError, self.db.createaggregatefunction, "twelve", longest.factory, 923) # max args is 127
        self.assertRaises(TypeError, self.db.createaggregatefunction,u"twelve\N{BLACK STAR}", 12) # must be ascii
        self.db.createaggregatefunction("twelve", None)
        self.db.createaggregatefunction("longest", longest.factory)

        vals=(
            ("kjfhgk","gkjlfdhgjkhsdfkjg","gklsdfjgkldfjhnbnvc,mnxb,mnxcv,mbncv,mnbm,ncvx,mbncv,mxnbcv,"), # last one is deliberately the longest
            ("gdfklhj",":gjkhgfdsgfd","gjkfhgjkhdfkjh"),
            ("gdfjkhg","gkjlfd",""),
            (1,2,30),
           )

        for v in vals:
            c.execute("insert into foo values(?,?,?)", v)

        v=c.execute("select longest(x,y,z) from foo").next()[0]
        self.failUnlessEqual(v, vals[0][2])

        # SQLite doesn't allow step functions to return an error, so we have to defer to the final
        def badfactory():
            def badfunc(*args):
                1/0
            def final(*args):
                self.fail("This should not be executed")
                return 1
            return None,badfunc,final
        
        self.db.createaggregatefunction("badfunc", badfactory)
        self.assertRaises(ZeroDivisionError, c.execute, "select badfunc(x) from foo")

        # error in final
        def badfactory():
            def badfunc(*args):
                pass
            def final(*args):
                1/0
            return None,badfunc,final
        self.db.createaggregatefunction("badfunc", badfactory)
        self.assertRaises(ZeroDivisionError, c.execute, "select badfunc(x) from foo")

        # error in step and final
        def badfactory():
            def badfunc(*args):
                1/0
            def final(*args):
                raise ImportError() # zero div from above is what should be returned
            return None,badfunc,final
        self.db.createaggregatefunction("badfunc", badfactory)
        self.assertRaises(ZeroDivisionError, c.execute, "select badfunc(x) from foo")

        # bad return from factory
        def badfactory():
            def badfunc(*args):
                pass
            def final(*args):
                return 0
            return {}
        self.db.createaggregatefunction("badfunc", badfactory)
        self.assertRaises(TypeError, c.execute, "select badfunc(x) from foo")

        # incorrect number of items returned
        def badfactory():
            def badfunc(*args):
                pass
            def final(*args):
                return 0
            return (None, badfunc, final, badfactory)
        self.db.createaggregatefunction("badfunc", badfactory)
        self.assertRaises(TypeError, c.execute, "select badfunc(x) from foo")

        # step not callable
        def badfactory():
            def badfunc(*args):
                pass
            def final(*args):
                return 0
            return (None, True, final )
        self.db.createaggregatefunction("badfunc", badfactory)
        self.assertRaises(TypeError, c.execute, "select badfunc(x) from foo")

        # final not callable
        def badfactory():
            def badfunc(*args):
                pass
            def final(*args):
                return 0
            return (None, badfunc, True )
        self.db.createaggregatefunction("badfunc", badfactory)
        self.assertRaises(TypeError, c.execute, "select badfunc(x) from foo")

        # error in factory method
        def badfactory():
            1/0
        self.db.createaggregatefunction("badfunc", badfactory)
        self.assertRaises(ZeroDivisionError, c.execute, "select badfunc(x) from foo")

        
    def testCollation(self):
        "Verify collations"
        c=self.db.cursor()
        def strnumcollate(s1, s2):
            "return -1 if s1<s2, +1 if s1>s2 else 0.  Items are string head and numeric tail"
            # split values into two parts - the head and the numeric tail
            values=[s1,s2]
            for vn,v in enumerate(values):
                for i in range(len(v),0,-1):
                    if v[i-1] not in "01234567890":
                        break
                try:
                    v=v[:i],int(v[i:])
                except ValueError:
                    v=v[:i],None
                values[vn]=v
            # compare
            if values[0]<values[1]:
                return -1
            if values[0]>values[1]:
                return 1
            return 0

        self.assertRaises(TypeError, self.db.createcollation, "twelve", strnumcollate, 12) # wrong # params
        self.assertRaises(TypeError, self.db.createcollation, "twelve", 12) # must be callable
        self.assertRaises(TypeError, self.db.createcollation,u"twelve\N{BLACK STAR}", strnumcollate) # must be ascii
        self.db.createcollation("strnum", strnumcollate)
        c.execute("create table foo(x)")
        # adding this unicode in front improves coverage
        uni=u"\N{LATIN SMALL LETTER E WITH CIRCUMFLEX}"
        vals=(uni+"file1", uni+"file7", uni+"file9", uni+"file17", uni+"file20")
        valsrev=list(vals)
        valsrev.reverse() # put them into table in reverse order
        c.executemany("insert into foo values(?)", [(x,) for x in valsrev])
        for i,row in enumerate(c.execute("select x from foo order by x collate strnum")):
            self.failUnlessEqual(vals[i], row[0])

        # collation function with an error
        def collerror(*args):
            return 1/0
        self.db.createcollation("collerror", collerror)
        self.assertRaises(ZeroDivisionError, c.execute, "select x from foo order by x collate collerror")

        # collation function that returns bad value
        def collerror(*args):
            return {}
        self.db.createcollation("collbadtype", collerror)
        self.assertRaises(TypeError, c.execute, "select x from foo order by x collate collbadtype")

        # get error when registering
        c.execute("select x from foo order by x collate strnum") # nb we don't read so cursor is still active
        self.assertRaises(apsw.BusyError, self.db.createcollation, "strnum", strnumcollate)

        # unregister
        for row in c: pass
        self.db.createcollation("strnum", None)
        # check it really has gone
        self.assertRaises(apsw.SQLError, c.execute, "select x from foo order by x collate strnum")
        # check statement still works
        for _ in c.execute("select x from foo"): pass
        
    def testProgressHandler(self):
        "Verify progress handler"
        c=self.db.cursor()
        phcalledcount=[0]
        def ph():
            phcalledcount[0]=phcalledcount[0]+1
            return 0

        # make 400 rows of random numbers
        c.execute("begin ; create table foo(x)")
        c.executemany("insert into foo values(?)", randomintegers(400))
        c.execute("commit")

        self.assertRaises(TypeError, self.db.setprogresshandler, 12) # must be callable
        self.assertRaises(TypeError, self.db.setprogresshandler, ph, "foo") # second param is steps
        self.db.setprogresshandler(ph, -17) # SQLite doesn't complain about negative numbers
        self.db.setprogresshandler(ph, 20)
        c.execute("select max(x) from foo").next()

        self.assertNotEqual(phcalledcount[0], 0)
        saved=phcalledcount[0]

        # put an error in the progress handler
        def ph(): return 1/0
        self.db.setprogresshandler(ph, 1)
        self.assertRaises(ZeroDivisionError, c.execute, "update foo set x=-10")
        self.db.setprogresshandler(None) # clear ph so next line runs
        # none should have taken
        self.failUnlessEqual(0, c.execute("select count(*) from foo where x=-10").next()[0])
        # and previous ph should not have been called
        self.failUnlessEqual(saved, phcalledcount[0])
        def ph():
            return BadIsTrue()
        self.db.setprogresshandler(ph, 1)
        self.assertRaises(ZeroDivisionError, c.execute, "update foo set x=-10")

    def testChanges(self):
        "Verify reporting of changes"
        c=self.db.cursor()
        c.execute("create table foo (x);begin")
        for i in xrange(100):
            c.execute("insert into foo values(?)", (i+1000,))
        c.execute("commit")
        c.execute("update foo set x=0 where x>=1000")
        self.failUnlessEqual(100, self.db.changes())
        c.execute("begin")
        for i in xrange(100):
            c.execute("insert into foo values(?)", (i+1000,))
        c.execute("commit")
        self.failUnlessEqual(300, self.db.totalchanges())

    def testLastInsertRowId(self):
        "Check last insert row id"
        c=self.db.cursor()
        c.execute("create table foo (x integer primary key)")
        for i in range(10):
            c.execute("insert into foo values(?)", (i,))
            self.failUnlessEqual(i, self.db.last_insert_rowid())
        # get a 64 bit value
        v=2**40
        c.execute("insert into foo values(?)", (v,))
        self.failUnlessEqual(v, self.db.last_insert_rowid())

    def testComplete(self):
        "Completeness of SQL statement checking"
        # the actual underlying routine just checks that there is a semi-colon
        # at the end, not inside any quotes etc
        self.failUnlessEqual(False, self.db.complete("select * from"))
        self.failUnlessEqual(False, self.db.complete("select * from \";\""))
        self.failUnlessEqual(False, self.db.complete("select * from \";"))
        self.failUnlessEqual(True, self.db.complete("select * from foo; select *;"))
        self.failUnlessEqual(False, self.db.complete("select * from foo where x=1"))
        self.failUnlessEqual(True, self.db.complete("select * from foo;"))
        self.failUnlessEqual(True, self.db.complete(u"select '\u9494\ua7a7';"))
        self.assertRaises(UnicodeDecodeError, self.db.complete, "select '\x94\xa7';")
        self.assertRaises(TypeError, self.db.complete, 12) # wrong type
        self.assertRaises(TypeError, self.db.complete)     # not enough args
        self.assertRaises(TypeError, self.db.complete, "foo", "bar") # too many args

    def testBusyHandling(self):
        "Verify busy handling"
        c=self.db.cursor()
        c.execute("create table foo(x); begin")
        c.executemany("insert into foo values(?)", randomintegers(400))
        c.execute("commit")
        # verify it is blocked
        db2=apsw.Connection("testdb")
        c2=db2.cursor()
        c2.execute("begin exclusive")
        self.assertRaises(apsw.BusyError, c.execute, "begin immediate ; select * from foo")

        # close and reopen databases - sqlite will return Busy immediately to a connection
        # it previously returned busy to
        del c
        del c2
        db2.close()
        self.db.close()
        del db2
        del self.db
        self.db=apsw.Connection("testdb")
        db2=apsw.Connection("testdb")
        c=self.db.cursor()
        c2=db2.cursor()
        
        # Put in busy handler
        bhcalled=[0]
        def bh(*args):
            bhcalled[0]=bhcalled[0]+1
            if bhcalled[0]==4:
                return False
            return True
        self.assertRaises(TypeError, db2.setbusyhandler, 12) # must be callable
        self.assertRaises(TypeError, db2.setbusytimeout, "12") # must be int
        db2.setbusytimeout(-77)  # SQLite doesn't complain about negative numbers, but if it ever does this will catch it
        self.assertRaises(TypeError, db2.setbusytimeout, 77,88) # too many args
        self.db.setbusyhandler(bh)

        c2.execute("begin exclusive")
        
        try:
            for row in c.execute("begin immediate ; select * from foo"):
                print row
        except apsw.BusyError:
            pass
        self.failUnlessEqual(bhcalled[0], 4)

        # Close and reopen again
        del c
        del c2
        db2.close()
        self.db.close()
        del db2
        del self.db
        self.db=apsw.Connection("testdb")
        db2=apsw.Connection("testdb")
        c=self.db.cursor()
        c2=db2.cursor()
        
        # Put in busy timeout
        TIMEOUT=3 # seconds, must be integer as sqlite can round down to nearest second anyway
        c2.execute("begin exclusive")
        self.assertRaises(TypeError, self.db.setbusyhandler, "foo")
        self.db.setbusytimeout(int(TIMEOUT*1000))
        b4=time.time()
        try:
            c.execute("begin immediate ; select * from foo")
        except apsw.BusyError:
            pass
        after=time.time()
        self.failUnless(after-b4>=TIMEOUT)

        # check clearing of handler
        c2.execute("rollback")
        self.db.setbusyhandler(None)
        b4=time.time()
        c2.execute("begin exclusive")
        try:
            c.execute("begin immediate ; select * from foo")
        except apsw.BusyError:
            pass
        after=time.time()
        self.failUnless(after-b4<TIMEOUT)

        # Close and reopen again
        del c
        del c2
        db2.close()
        self.db.close()
        del db2
        del self.db
        self.db=apsw.Connection("testdb")
        db2=apsw.Connection("testdb")
        c=self.db.cursor()
        c2=db2.cursor()

        # error in busyhandler
        def bh(*args):
            1/0
        c2.execute("begin exclusive")
        self.db.setbusyhandler(bh)
        self.assertRaises(ZeroDivisionError, c.execute, "begin immediate ; select * from foo")
        del c
        del c2
        db2.close()

        def bh(*args):
            return BadIsTrue()
        db2=apsw.Connection("testdb")
        c=self.db.cursor()
        c2=db2.cursor()
        c2.execute("begin exclusive")
        self.db.setbusyhandler(bh)
        self.assertRaises(ZeroDivisionError, c.execute, "begin immediate ; select * from foo")
        del c
        del c2
        db2.close()        

    def testBusyHandling2(self):
        "Another busy handling test"

        # Based on an issue in 3.3.10 and before
        con2=apsw.Connection("testdb")
        cur=self.db.cursor()
        cur2=con2.cursor()
        cur.execute("create table test(x,y)")
        cur.execute("begin")
        cur.execute("insert into test values(123,'abc')")
        self.assertRaises(apsw.BusyError, cur2.execute, "insert into test values(456, 'def')")
        cur.execute("commit")
        self.assertEqual(1, cur2.execute("select count(*) from test where x=123").next()[0])
        con2.close()

    def testInterruptHandling(self):
        "Verify interrupt function"
        # this is tested by having a user defined function make the interrupt
        c=self.db.cursor()
        c.execute("create table foo(x);begin")
        c.executemany("insert into foo values(?)", randomintegers(400))
        c.execute("commit")
        def ih(*args):
            self.db.interrupt()
            return 7
        self.db.createscalarfunction("seven", ih)
        try:
            for row in c.execute("select seven(x) from foo"):
                pass
        except apsw.InterruptError:
            pass

    def testCommitHook(self):
        "Verify commit hooks"
        c=self.db.cursor()
        c.execute("create table foo(x)")
        c.executemany("insert into foo values(?)", randomintegers(10))
        chcalled=[0]
        def ch():
            chcalled[0]=chcalled[0]+1
            if chcalled[0]==4:
                return 1 # abort
            return 0 # continue
        self.assertRaises(TypeError, self.db.setcommithook, 12)  # not callable
        self.db.setcommithook(ch)
        self.assertRaises(apsw.ConstraintError, c.executemany, "insert into foo values(?)", randomintegers(10))
        self.assertEqual(4, chcalled[0])
        self.db.setcommithook(None)
        def ch():
            chcalled[0]=99
            return 1
        self.db.setcommithook(ch)
        self.assertRaises(apsw.ConstraintError, c.executemany, "insert into foo values(?)", randomintegers(10))
        # verify it was the second one that was called
        self.assertEqual(99, chcalled[0])
        # error in commit hook
        def ch():
            return 1/0
        self.db.setcommithook(ch)
        self.assertRaises(ZeroDivisionError, c.execute, "insert into foo values(?)", (1,))
        def ch():
            return BadIsTrue()
        self.db.setcommithook(ch)
        self.assertRaises(ZeroDivisionError, c.execute, "insert into foo values(?)", (1,))
        

    def testRollbackHook(self):
        "Verify rollback hooks"
        c=self.db.cursor()
        c.execute("create table foo(x)")
        rhcalled=[0]
        def rh():
            rhcalled[0]=rhcalled[0]+1
            return 1
        self.assertRaises(TypeError, self.db.setrollbackhook, 12) # must be callable
        self.db.setrollbackhook(rh)
        c.execute("begin ; insert into foo values(10); rollback")
        self.assertEqual(1, rhcalled[0])
        self.db.setrollbackhook(None)
        c.execute("begin ; insert into foo values(10); rollback")
        self.assertEqual(1, rhcalled[0])
        def rh():
            1/0
        self.db.setrollbackhook(rh)
        # SQLite doesn't allow reporting an error from a rollback hook, so it will be seen
        # in the next command (eg the select in this case)
        self.assertRaises(ZeroDivisionError, c.execute, "begin ; insert into foo values(10); rollback; select * from foo")
        # check cursor still works
        for row in c.execute("select * from foo"):
            pass

    def testUpdateHook(self):
        "Verify update hooks"
        c=self.db.cursor()
        c.execute("create table foo(x integer primary key, y)")
        uhcalled=[]
        def uh(type, databasename, tablename, rowid):
            uhcalled.append( (type, databasename, tablename, rowid) )
        self.assertRaises(TypeError, self.db.setupdatehook, 12) # must be callable
        self.db.setupdatehook(uh)
        statements=(
            ("insert into foo values(3,4)", (apsw.SQLITE_INSERT, 3) ),
            ("insert into foo values(30,40)", (apsw.SQLITE_INSERT, 30) ),
            ("update foo set y=47 where x=3", (apsw.SQLITE_UPDATE, 3), ),
            ("delete from foo where y=47", (apsw.SQLITE_DELETE, 3), ),
            )
        for sql,res in statements:
            c.execute(sql)
        results=[(type, "main", "foo", rowid) for sql,(type,rowid) in statements]
        self.assertEqual(uhcalled, results)
        self.db.setupdatehook(None)
        c.execute("insert into foo values(99,99)")
        self.assertEqual(len(uhcalled), len(statements)) # length should have remained the same
        def uh(*args):
            1/0
        self.db.setupdatehook(uh)
        self.assertRaises(ZeroDivisionError, c.execute, "insert into foo values(100,100)")
        self.db.setupdatehook(None)
        # improve code coverage
        c.execute("create table bar(x,y); insert into bar values(1,2); insert into bar values(3,4)")
        def uh(*args):
            1/0
        self.db.setupdatehook(uh)
        self.assertRaises(ZeroDivisionError, c.execute, "insert into foo select * from bar")
        self.db.setupdatehook(None)
        
        # check cursor still works
        c.execute("insert into foo values(1000,1000)")
        self.assertEqual(1, c.execute("select count(*) from foo where x=1000").next()[0])

    def testProfile(self):
        "Verify profiling"
        # we do the test by looking for the maximum of 100,000 random
        # numbers with an index present and without.  The former
        # should be way quicker.
        c=self.db.cursor()
        c.execute("create table foo(x); begin")
        c.executemany("insert into foo values(?)", randomintegers(PROFILESTEPS))
        profileinfo=[]
        def profile(statement, timing):
            profileinfo.append( (statement, timing) )
        c.execute("commit; create index foo_x on foo(x)")
        self.assertRaises(TypeError, self.db.setprofile, 12) # must be callable
        self.db.setprofile(profile)
        for val1 in c.execute("select max(x) from foo"): pass # profile is only run when results are exhausted
        self.db.setprofile(None)
        c.execute("drop index foo_x")
        self.db.setprofile(profile)
        for val2 in c.execute("select max(x) from foo"): pass
        self.failUnlessEqual(val1, val2)
        self.failUnless(len(profileinfo)>=2) # see SQLite ticket 2157
        self.failUnlessEqual(profileinfo[0][0], profileinfo[-1][0])
        self.failUnlessEqual("select max(x) from foo", profileinfo[0][0])
        self.failUnlessEqual("select max(x) from foo", profileinfo[-1][0])
        # the query using the index should take way less time
        self.failUnless(profileinfo[0][1]<profileinfo[-1][1])
        def profile(*args):
            1/0
        self.db.setprofile(profile)
        self.assertRaises(ZeroDivisionError, c.execute, "create table bar(y)")
        # coverage
        wasrun=[False]
        def profile(*args):
            wasrun[0]=True
        def uh(*args): 1/0
        self.db.setprofile(profile)
        self.db.setupdatehook(uh)
        self.assertRaises(ZeroDivisionError, c.execute, "insert into foo values(3)")
        self.failUnlessEqual(wasrun[0], False)
        self.db.setprofile(None)
        self.db.setupdatehook(None)

    def testThreading(self):
        "Verify threading behaviour"
        # We used to require all operations on a connection happen in
        # the same thread.  Now they can happen in any thread, so we
        # ensure that inuse errors are detected by doing a long
        # running operation in one thread.
        c=self.db.cursor()
        c.execute("create table foo(x);begin;")
        c.executemany("insert into foo values(?)", randomintegers(100000))
        c.execute("commit")

        vals={"stop": False,
              "raised": False}
        def wt():
            try:
                while not vals["stop"]:
                    c.execute("select min(max(x-1+x),min(x-1+x)) from foo")
            except apsw.ThreadingViolationError:
                vals["raised"]=True
                vals["stop"]=True
                
        t=ThreadRunner(wt)
        t.start()
        # ensure thread t has started
        time.sleep(0.1)
        b4=time.time()
        # try to get a threadingviolation for 30 seconds
        try:
            try:
                while not vals["stop"] and time.time()-b4<30:
                    c.execute("select * from foo")
            except apsw.ThreadingViolationError:
                vals["stop"]=True
                vals["raised"]=True
        finally:
            vals["stop"]=True
        t.go()
        self.assertEqual(vals["raised"], True)

    def testStringsWithNulls(self):
        "Verify that strings with nulls in them are handled correctly"

        c=self.db.cursor()
        c.execute("create table foo(row,str)")
        vals=("a simple string",
              "a simple string\0with a null",
              "a string\0with two\0nulls",
              "or even a \0\0\0\0\0\0sequence\0\0\0\0\of them",
              u"a \u1234 unicode \ufe54 string \u0089",
              u"a \u1234 unicode \ufe54 string \u0089\0and some text",
              u"\N{BLACK STAR} \N{WHITE STAR} \N{LIGHTNING} \N{COMET}\0more\0than you\0can handle",
              u"\N{BLACK STAR} \N{WHITE STAR} \N{LIGHTNING} \N{COMET}\0\0\0\0\0sequences\0\0\0of them")

        # See http://www.sqlite.org/cvstrac/tktview?tn=3056
        if True: # [int(x) for x in apsw.sqlitelibversion().split(".")]<[3,5,8]:
            vals=vals+(
              "a simple string\0",
              u"a \u1234 unicode \ufe54 string \u0089\0",
              )

        for i,v in enumerate(vals):
            c.execute("insert into foo values(?,?)", (i, v))

        self.assertRaises(UnicodeDecodeError, c.execute, "insert into foo values(9000,?)", ("a simple string\0with a null and \xfe\xfb\x80\x92",))
            
        # add function to test conversion back as well
        def snap(*args):
            return args[0]
        self.db.createscalarfunction("snap", snap)

        # now see what we got out
        count=0
        for row,v,fv in c.execute("select row,str,snap(str) from foo"):
            count+=1
            self.failUnlessEqual(vals[row], v)
            self.failUnlessEqual(vals[row], fv)
        self.failUnlessEqual(count, len(vals))

        # check execute
        for v in vals:
            self.failUnlessEqual(v, c.execute("select ?", (v,)).next()[0])
            # nulls not allowed in main query string, so lets check the other bits (unicode etc)
            v2=v.replace("\0", " zero ")
            self.failUnlessEqual(v2, c.execute("select '%s'" % (v2,)).next()[0])

        # ::TODO:: check collations

    def testSharedCache(self):
        "Verify setting of shared cache"

        # check parameters - wrong # or type of args
        self.assertRaises(TypeError, apsw.enablesharedcache)
        self.assertRaises(TypeError, apsw.enablesharedcache, "foo")
        self.assertRaises(TypeError, apsw.enablesharedcache, True, None)

        # the setting can be changed at almost any time
        apsw.enablesharedcache(True)
        apsw.enablesharedcache(False)

    def testTracebacks(self):
        "Verify augmented tracebacks"
        return
        def badfunc(*args):
            1/0
        self.db.createscalarfunction("badfunc", badfunc)
        try:
            c=self.db.cursor()
            c.execute("select badfunc()")
            self.fail("Exception should have occurred")
        except ZeroDivisionError:
            tb=sys.exc_info()[2]
            traceback.print_tb(tb)
            del tb
        except:
            self.fail("Wrong exception type")

    def testLoadExtension(self):
        "Check loading of extensions"
        # unicode issues
        self.assertRaises(UnicodeDecodeError, self.db.loadextension, "\xa7\x94")
        # they need to be enabled first (off by default)
        self.assertRaises(apsw.ExtensionLoadingError, self.db.loadextension, LOADEXTENSIONFILENAME)
        self.db.enableloadextension(False)
        self.assertRaises(ZeroDivisionError, self.db.enableloadextension, BadIsTrue())
        # should still be disabled
        self.assertRaises(apsw.ExtensionLoadingError, self.db.loadextension, LOADEXTENSIONFILENAME)
        self.db.enableloadextension(True)
        # make sure it checks args
        self.assertRaises(TypeError, self.db.loadextension)
        self.assertRaises(TypeError, self.db.loadextension, 12)
        self.assertRaises(TypeError, self.db.loadextension, "foo", 12)
        self.assertRaises(TypeError, self.db.loadextension, "foo", "bar", 12)
        self.db.loadextension(LOADEXTENSIONFILENAME)
        c=self.db.cursor()
        self.failUnlessEqual(1, c.execute("select half(2)").next()[0])
        # second entry point hasn't been called yet
        self.assertRaises(apsw.SQLError, c.execute, "select doubleup(2)")
        # load using other entry point
        self.assertRaises(apsw.ExtensionLoadingError, self.db.loadextension, LOADEXTENSIONFILENAME, "doesntexist")
        self.db.loadextension(LOADEXTENSIONFILENAME, "alternate_sqlite3_extension_init")
        self.failUnlessEqual(4, c.execute("select doubleup(2)").next()[0])
        

    def testVtables(self):
        "Test virtual table functionality"

        data=( # row 0 is headers, column 0 is rowid
            ( "rowid",     "name",    "number", "item",          "description"),
            ( 1,           "Joe Smith",    1.1, u"\u00f6\u1234", "foo"),
            ( 6000000000L, "Road Runner", -7.3, u"\u00f6\u1235", "foo"),
            ( 77,          "Fred",           0, u"\u00f6\u1236", "foo"),
            )

        dataschema="create table this_should_be_ignored"+`data[0][1:]`
        # a query that will get constraints on every column
        allconstraints="select rowid,* from foo where rowid>-1000 and name>='A' and number<=12.4 and item>'A' and description=='foo' order by item"
        allconstraintsl=[(-1, apsw.SQLITE_INDEX_CONSTRAINT_GT), # rowid >
                         ( 0, apsw.SQLITE_INDEX_CONSTRAINT_GE), # name >=
                         ( 1, apsw.SQLITE_INDEX_CONSTRAINT_LE), # number <=
                         ( 2, apsw.SQLITE_INDEX_CONSTRAINT_GT), # item >
                         ( 3, apsw.SQLITE_INDEX_CONSTRAINT_EQ), # description ==
                         ]
        

        # The testing uses a different module name each time.  SQLite
        # doc doesn't define the semantics if a 2nd module is
        # registered with the same name as an existing one and I was
        # getting coredumps.  It looks like issues inside SQLite.

        cur=self.db.cursor()
        # should fail since module isn't registered
        self.assertRaises(apsw.SQLError, cur.execute, "create virtual table vt using testmod(x,y,z)")
        # wrong args
        self.assertRaises(TypeError, self.db.createmodule, 1,2,3)
        # give a bad object
        self.db.createmodule("testmod", 12) # next line fails due to lack of Create method
        self.assertRaises(AttributeError, cur.execute, "create virtual table xyzzy using testmod(x,y,z)")

        class Source:
            def __init__(self, *expectargs):
                self.expectargs=expectargs
                
            def Create(self, *args): # db, modname, dbname, tablename, args
                if self.expectargs!=args[1:]:
                    raise ValueError("Create arguments are not correct.  Expected "+`self.expectargs`+" but got "+`args[1:]`)
                1/0

            def CreateErrorCode(self, *args):
                # This makes sure that sqlite error codes happen.  The coverage checker
                # is what verifies the code actually works.
                raise apsw.BusyError("foo")

            def CreateUnicodeException(self, *args):
                raise Exception(u"\N{LATIN SMALL LETTER E WITH CIRCUMFLEX}\N{LATIN SMALL LETTER A WITH TILDE}\N{LATIN SMALL LETTER O WITH DIAERESIS}")

            def CreateBadSchemaType(self, *args):
                return 12, None

            def CreateBadSchema(self, *args):
                return "this isn't remotely valid sql", None

            def CreateWrongNumReturns(self, *args):
                return "way","too","many","items",3

            def CreateBadSequence(self, *args):
                class badseq(object):
                    def __getitem__(self, which):
                        if which!=0:
                            1/0
                        return 12

                    def __len__(self):
                        return 2
                return badseq()

        # check Create does the right thing - we don't include db since it creates a circular reference
        self.db.createmodule("testmod1", Source("testmod1", "main", "xyzzy", "1", '"one"'))
        self.assertRaises(ZeroDivisionError, cur.execute, 'create virtual table xyzzy using testmod1(1,"one")')
        # unicode
        uni=u"\N{LATIN SMALL LETTER E WITH CIRCUMFLEX}\N{LATIN SMALL LETTER A WITH TILDE}\N{LATIN SMALL LETTER O WITH DIAERESIS}"
        self.db.createmodule("testmod1dash1", Source("testmod1dash1", "main", uni, "1", u'"'+uni+u'"'))
        self.assertRaises(ZeroDivisionError, cur.execute, u'create virtual table %s using testmod1dash1(1,"%s")' % (uni, uni))
        Source.Create=Source.CreateErrorCode
        self.assertRaises(apsw.BusyError, cur.execute, 'create virtual table xyzzz using testmod1(2, "two")')
        Source.Create=Source.CreateUnicodeException
        self.assertRaises(Exception, cur.execute, 'create virtual table xyzzz using testmod1(2, "two")')
        Source.Create=Source.CreateBadSchemaType
        self.assertRaises(TypeError, cur.execute, 'create virtual table xyzzz using testmod1(2, "two")')
        Source.Create=Source.CreateBadSchema
        self.assertRaises(apsw.SQLError, cur.execute, 'create virtual table xyzzz2 using testmod1(2, "two")')
        Source.Create=Source.CreateWrongNumReturns
        self.assertRaises(TypeError, cur.execute, 'create virtual table xyzzz2 using testmod1(2, "two")')
        Source.Create=Source.CreateBadSequence
        self.assertRaises(ZeroDivisionError, cur.execute, 'create virtual table xyzzz2 using testmod1(2, "two")')

        # a good version of Source
        class Source:
            def Create(self, *args):
                return dataschema, VTable(list(data))
            Connect=Create

        class VTable:

            # A set of results from bestindex which should all generate TypeError.
            # Coverage checking will ensure all the code is appropriately tickled
            badbestindex=(12,
                          (12,),
                          ((),),
                          (((),),),
                          ((((),),),),
                          (((((),),),),),
                          ((None,None,None,None,"bad"),),
                          ((0,None,(0,),None,None),),
                          ((("bad",True),None,None,None,None),),
                          (((0, True),"bad",None,None,None),),
                          (None,"bad"),
                          [4,(3,True),[2,False],1, [0]],
                          )
            numbadbextindex=len(badbestindex)

            def __init__(self, data):
                self.data=data
                self.bestindex3val=0

            def BestIndex1(self, wrong, number, of, arguments):
                1/0

            def BestIndex2(self, *args):
                1/0

            def BestIndex3(self, constraints, orderbys):
                retval=self.badbestindex[self.bestindex3val]
                self.bestindex3val+=1
                if self.bestindex3val>=self.numbadbextindex:
                    self.bestindex3val=0
                return retval

            def BestIndex4(self, constraints, orderbys):
                # this gives ValueError ("bad" is not a float)
                return (None,12,u"\N{LATIN SMALL LETTER E WITH CIRCUMFLEX}", "anything", "bad")

            def BestIndex5(self, constraints, orderbys):
                # unicode error
                return (None, None, "\xde\xad\xbe\xef")

            def BestIndex6(self, constraints, orderbys):
                return ( (0, 1, (2, BadIsTrue()), 3, 4), )

            def BestIndex7(self, constraints, orderbys):
                return (None, None, "foo", BadIsTrue(), 99)

            _bestindexreturn=99
                
            def BestIndex99(self, constraints, orderbys):
                cl=list(constraints)
                cl.sort()
                assert allconstraintsl == cl
                assert orderbys == ( (2, False), )
                retval=( [4,(3,True),[2,False],1, (0, False)], 997, u"\N{LATIN SMALL LETTER E WITH CIRCUMFLEX}", False, 99)[:self._bestindexreturn]
                return retval

            def BestIndexGood(self, constraints, orderbys):
                return None

            def BestIndexGood2(self, constraints, orderbys):
                return [] # empty list is same as None

            def Open(self):
                return Cursor(self)

            def Open1(self, wrong, number, of, arguments):
                1/0

            def Open2(self):
                1/0

            def Open3(self):
                return None

            def Open99(self):
                return Cursor(self)

            UpdateInsertRow1=None
                        
            def UpdateInsertRow2(self, too, many, args):
                1/0

            def UpdateInsertRow3(self, rowid, fields):
                1/0

            def UpdateInsertRow4(self, rowid, fields):
                assert rowid is None
                return None
                
            def UpdateInsertRow5(self, rowid, fields):
                assert rowid is None
                return "this is not a number"

            def UpdateInsertRow6(self, rowid, fields):
                assert rowid is None
                return -922337203685477580799L # too big

            def UpdateInsertRow7(self, rowid, fields):
                assert rowid is None
                return 9223372036854775807L # ok

            def UpdateInsertRow8(self, rowid, fields):
                assert rowid is not None
                assert rowid==-12
                return "this should be ignored since rowid was supplied"

            def UpdateChangeRow1(self, too, many, args, methinks):
                1/0

            def UpdateChangeRow2(self, rowid, newrowid, fields):
                1/0

            def UpdateChangeRow3(self, rowid, newrowid, fields):
                assert newrowid==rowid

            def UpdateChangeRow4(self, rowid, newrowid, fields):
                assert newrowid==rowid+20

            def UpdateDeleteRow1(self, too, many, args):
                1/0

            def UpdateDeleteRow2(self, rowid):
                1/0

            def UpdateDeleteRow3(self, rowid):
                assert rowid==77

            def Disconnect1(self, too, many, args):
                1/0

            def Disconnect2(self):
                1/0

            def Disconnect3(self):
                pass

            def Destroy1(self, too, many, args):
                1/0
                
            def Destroy2(self):
                1/0

            def Destroy3(self):
                pass

            def Begin1(self, too, many, args):
                1/0

            def Begin2(self):
                1/0

            def Begin3(self):
                pass

            def Sync(self):
                pass

            def Commit(self):
                pass

            def Rollback(self):
                pass

        class Cursor:

            _bestindexreturn=99

            def __init__(self, table):
                self.table=table

            def Filter1(self, toofewargs):
                1/0

            def Filter2(self, *args):
                1/0

            def Filter99(self, idxnum, idxstr, constraintargs):
                self.pos=1 # row 0 is headers
                if self._bestindexreturn==0:
                    assert idxnum==0
                    assert idxstr==None
                    assert constraintargs==()
                    return
                if self._bestindexreturn==1:
                    assert idxnum==0
                    assert idxstr==None
                    assert constraintargs==('A', 12.4, 'A', -1000)
                    return
                if self._bestindexreturn==2:
                    assert idxnum==997
                    assert idxstr==None
                    assert constraintargs==('A', 12.4, 'A', -1000)
                    return 
                # 3 or more
                assert idxnum==997
                assert idxstr==u"\N{LATIN SMALL LETTER E WITH CIRCUMFLEX}"
                assert constraintargs==('A', 12.4, 'A', -1000)

            def Filter(self,  *args):
                self.Filter99(*args)
                1/0

            def FilterGood(self, *args):
                self.pos=1 # row 0 is headers

            def Eof1(self, toomany, args):
                1/0

            def Eof2(self):
                1/0

            def Eof3(self):
                return BadIsTrue()

            def Eof99(self):
                return not ( self.pos<len(self.table.data) )

            def Rowid1(self, too, many, args):
                1/0

            def Rowid2(self):
                1/0

            def Rowid3(self):
                return "cdrom"

            def Rowid99(self):
                return self.table.data[self.pos][0]

            def Column1(self):
                1/0

            def Column2(self, too, many, args):
                1/0

            def Column3(self, col):
                1/0

            def Column4(self, col):
                return self # bad type

            def Column99(self, col):
                return self.table.data[self.pos][col+1] # col 0 is row id

            def Close1(self, too, many, args):
                1/0

            def Close2(self):
                1/0

            def Close99(self):
                del self.table  # deliberately break ourselves

            def Next1(self, too, many, args):
                1/0

            def Next2(self):
                1/0

            def Next99(self):
                self.pos+=1

        # use our more complete version
        self.db.createmodule("testmod2", Source())
        cur.execute("create virtual table foo using testmod2(2,two)")
        # are missing/mangled methods detected correctly?
        self.assertRaises(AttributeError, cur.execute, "select rowid,* from foo order by number")
        VTable.BestIndex=VTable.BestIndex1
        self.assertRaises(TypeError, cur.execute, "select rowid,* from foo order by number")
        VTable.BestIndex=VTable.BestIndex2
        self.assertRaises(ZeroDivisionError, cur.execute, "select rowid,* from foo order by number")
        # check bestindex results
        VTable.BestIndex=VTable.BestIndex3
        for i in range(VTable.numbadbextindex):
            self.assertRaises(TypeError, cur.execute, allconstraints)
        VTable.BestIndex=VTable.BestIndex4
        self.assertRaises(ValueError, cur.execute, allconstraints)
        VTable.BestIndex=VTable.BestIndex5
        self.assertRaises(UnicodeDecodeError, cur.execute, allconstraints)
        VTable.BestIndex=VTable.BestIndex6
        self.assertRaises(ZeroDivisionError, cur.execute, allconstraints)
        VTable.BestIndex=VTable.BestIndex7
        self.assertRaises(ZeroDivisionError, cur.execute, allconstraints)

        # check varying number of return args from bestindex
        VTable.BestIndex=VTable.BestIndex99
        for i in range(6):
            VTable._bestindexreturn=i
            Cursor._bestindexreturn=i
            try:
                cur.execute(" "+allconstraints+" "*i) # defeat statement cache - bestindex is called during prepare
            except ZeroDivisionError:
                pass

        # error cases ok, return real values and move on to cursor methods
        del VTable.Open
        del Cursor.Filter
        self.assertRaises(AttributeError, cur.execute, allconstraints) # missing open
        VTable.Open=VTable.Open1
        self.assertRaises(TypeError, cur.execute,allconstraints)
        VTable.Open=VTable.Open2
        self.assertRaises(ZeroDivisionError, cur.execute,allconstraints)
        VTable.Open=VTable.Open3
        self.assertRaises(AttributeError, cur.execute, allconstraints)
        VTable.Open=VTable.Open99
        self.assertRaises(AttributeError, cur.execute, allconstraints)
        # put in filter
        Cursor.Filter=Cursor.Filter1
        self.assertRaises(TypeError, cur.execute,allconstraints)
        Cursor.Filter=Cursor.Filter2
        self.assertRaises(ZeroDivisionError, cur.execute,allconstraints)
        Cursor.Filter=Cursor.Filter99
        self.assertRaises(AttributeError, cur.execute, allconstraints)
        Cursor.Eof=Cursor.Eof1
        self.assertRaises(TypeError, cur.execute, allconstraints)
        Cursor.Eof=Cursor.Eof2
        self.assertRaises(ZeroDivisionError,cur.execute, allconstraints)
        Cursor.Eof=Cursor.Eof3
        self.assertRaises(ZeroDivisionError,cur.execute, allconstraints)
        Cursor.Eof=Cursor.Eof99
        self.assertRaises(AttributeError, cur.execute, allconstraints)
        # now onto to rowid
        Cursor.Rowid=Cursor.Rowid1
        self.assertRaises(TypeError, cur.execute,allconstraints)
        Cursor.Rowid=Cursor.Rowid2
        self.assertRaises(ZeroDivisionError, cur.execute,allconstraints)
        Cursor.Rowid=Cursor.Rowid3
        self.assertRaises(ValueError, cur.execute,allconstraints)
        Cursor.Rowid=Cursor.Rowid99
        self.assertRaises(AttributeError, cur.execute, allconstraints)
        # column
        Cursor.Column=Cursor.Column1
        self.assertRaises(TypeError, cur.execute,allconstraints)
        Cursor.Column=Cursor.Column2
        self.assertRaises(TypeError, cur.execute,allconstraints)
        Cursor.Column=Cursor.Column3
        self.assertRaises(ZeroDivisionError, cur.execute,allconstraints)
        Cursor.Column=Cursor.Column4
        self.assertRaises(TypeError, cur.execute,allconstraints)
        Cursor.Column=Cursor.Column99
        try:
            for row in cur.execute(allconstraints): pass
        except AttributeError:  pass
        # next
        Cursor.Next=Cursor.Next1
        try:
            for row in cur.execute(allconstraints): pass
        except TypeError:  pass
        Cursor.Next=Cursor.Next2
        try:
            for row in cur.execute(allconstraints): pass
        except ZeroDivisionError:  pass
        Cursor.Next=Cursor.Next99
        try:
            for row in cur.execute(allconstraints): pass
        except AttributeError:
            pass
        # close
        Cursor.Close=Cursor.Close1
        try:
            for row in cur.execute(allconstraints): pass
        except TypeError:  pass
        Cursor.Close=Cursor.Close2
        try:
            for row in cur.execute(allconstraints): pass
        except ZeroDivisionError:  pass
        Cursor.Close=Cursor.Close99

        # update (insert)
        sql="insert into foo (name, description) values('gunk', 'foo')"
        self.assertRaises(AttributeError, cur.execute, sql)
        VTable.UpdateInsertRow=VTable.UpdateInsertRow1
        self.assertRaises(TypeError, cur.execute, sql)
        VTable.UpdateInsertRow=VTable.UpdateInsertRow2
        self.assertRaises(TypeError, cur.execute, sql)
        VTable.UpdateInsertRow=VTable.UpdateInsertRow3
        self.assertRaises(ZeroDivisionError, cur.execute, sql)
        VTable.UpdateInsertRow=VTable.UpdateInsertRow4
        self.assertRaises(TypeError, cur.execute, sql)
        VTable.UpdateInsertRow=VTable.UpdateInsertRow5
        self.assertRaises(ValueError, cur.execute, sql)
        VTable.UpdateInsertRow=VTable.UpdateInsertRow6
        self.assertRaises(OverflowError, cur.execute, sql)
        VTable.UpdateInsertRow=VTable.UpdateInsertRow7
        cur.execute(sql)
        self.failUnlessEqual(self.db.last_insert_rowid(), 9223372036854775807L)
        VTable.UpdateInsertRow=VTable.UpdateInsertRow8
        cur.execute("insert into foo (rowid,name, description) values(-12,'gunk', 'foo')")
        
        # update (change)
        VTable.BestIndex=VTable.BestIndexGood
        Cursor.Filter=Cursor.FilterGood
        sql="update foo set description=='bar' where description=='foo'"
        self.assertRaises(AttributeError, cur.execute, sql)
        VTable.UpdateChangeRow=VTable.UpdateChangeRow1
        self.assertRaises(TypeError, cur.execute, sql)
        VTable.UpdateChangeRow=VTable.UpdateChangeRow2
        self.assertRaises(ZeroDivisionError, cur.execute, sql)
        VTable.UpdateChangeRow=VTable.UpdateChangeRow3
        cur.execute(sql)
        VTable.UpdateChangeRow=VTable.UpdateChangeRow4
        cur.execute("update foo set rowid=rowid+20 where 1")

        # update (delete)
        VTable.BestIndex=VTable.BestIndexGood2  # improves code coverage
        sql="delete from foo where name=='Fred'"
        self.assertRaises(AttributeError, cur.execute, sql)
        VTable.UpdateDeleteRow=VTable.UpdateDeleteRow1
        self.assertRaises(TypeError, cur.execute, sql)
        VTable.UpdateDeleteRow=VTable.UpdateDeleteRow2
        self.assertRaises(ZeroDivisionError, cur.execute, sql)
        VTable.UpdateDeleteRow=VTable.UpdateDeleteRow3
        cur.execute(sql)

        # transaction control
        # Begin, Sync, Commit and rollback all use the same underlying code
        VTable.Begin=VTable.Begin1
        self.assertRaises(TypeError, cur.execute, sql)
        VTable.Begin=VTable.Begin2
        self.assertRaises(ZeroDivisionError, cur.execute, sql)
        VTable.Begin=VTable.Begin3
        cur.execute(sql)

        # disconnect - sqlite ignores any errors
        db=apsw.Connection("testdb")
        db.createmodule("testmod2", Source())
        cur2=db.cursor()
        for _ in cur2.execute("select * from foo"): pass
        VTable.Disconnect=VTable.Disconnect1
        self.assertRaises(TypeError, db.close) # nb close succeeds!
        self.assertRaises(apsw.ConnectionClosedError, cur2.execute, "select * from foo")
        del db
        db=apsw.Connection("testdb")
        db.createmodule("testmod2", Source())
        cur2=db.cursor()
        for _ in cur2.execute("select * from foo"): pass
        VTable.Disconnect=VTable.Disconnect2
        self.assertRaises(ZeroDivisionError, db.close) # nb close succeeds!
        self.assertRaises(apsw.ConnectionClosedError, cur2.execute, "select * from foo")
        del db
        db=apsw.Connection("testdb")
        db.createmodule("testmod2", Source())
        cur2=db.cursor()
        for _ in cur2.execute("select * from foo"): pass
        VTable.Disconnect=VTable.Disconnect3
        db.close()
        del db

        # destroy
        VTable.Destroy=VTable.Destroy1        
        self.assertRaises(TypeError, cur.execute, "drop table foo")
        VTable.Destroy=VTable.Destroy2
        self.assertRaises(ZeroDivisionError, cur.execute, "drop table foo")
        VTable.Destroy=VTable.Destroy3        
        cur.execute("drop table foo")
        self.db.close()

                            
    def testVTableExample(self):
        "Tests vtable example code"
        # Make sure vtable code actually works by comparing SQLite
        # results against manually computed results

        def getfiledata(directories):
            columns=None
            data=[]
            counter=1
            for directory in directories:
                for f in os.listdir(directory):
                    if not os.path.isfile(os.path.join(directory,f)):
                        continue
                    counter+=1
                    st=os.stat(os.path.join(directory,f))
                    if columns is None:
                        columns=["rowid", "name", "directory"]+[x for x in dir(st) if x.startswith("st_")]
                    data.append( [counter, f, directory] + [getattr(st,x) for x in columns[3:]] )
            return columns, data
        
        class Source:
            def Create(self, db, modulename, dbname, tablename, *args):
                columns,data=getfiledata([eval(a) for a in args]) # eval strips off layer of quotes
                schema="create table foo("+','.join(["'%s'" % (x,) for x in columns[1:]])+")"
                return schema,Table(columns,data)
            Connect=Create

        class Table:
            def __init__(self, columns, data):
                self.columns=columns
                self.data=data

            def BestIndex(self, *args):
                return None

            def Open(self):
                return Cursor(self)

            def Disconnect(self):
                pass

            Destroy=Disconnect

        class Cursor:
            def __init__(self, table):
                self.table=table

            def Filter(self, *args):
                self.pos=0

            def Eof(self):
                return self.pos>=len(self.table.data)

            def Rowid(self):
                return self.table.data[self.pos][0]

            def Column(self, col):
                return self.table.data[self.pos][1+col]

            def Next(self):
                self.pos+=1

            def Close(self):
                pass

        paths=[x.replace("\\","/") for x in sys.path if len(x) and os.path.isdir(x)]
        cols,data=getfiledata(paths)
        self.db.createmodule("filesource", Source())
        cur=self.db.cursor()
        args=",".join(["'%s'" % (x,) for x in paths])
        cur.execute("create virtual table files using filesource("+args+")")

        # Find the largest file (SQL)
        for bigsql in cur.execute("select st_size,name,directory from files order by st_size desc limit 1"):
            pass
        # Find the largest (manually)
        colnum=cols.index("st_size")
        bigmanual=(0,"","")
        for file in data:
            if file[colnum]>bigmanual[0]:
                bigmanual=file[colnum], file[1], file[2]

        self.failUnlessEqual(bigsql, bigmanual)

        # Find the oldest file (SQL)
        for oldestsql in cur.execute("select st_ctime,name,directory from files order by st_ctime limit 1"):
            pass
        # Find the oldest (manually)
        colnum=cols.index("st_ctime")
        oldestmanual=(99999999999999999L,"","")
        for file in data:
            if file[colnum]<oldestmanual[0]:
                oldestmanual=file[colnum], file[1], file[2]

        self.failUnlessEqual( oldestmanual, oldestsql)
                

    def testClosingChecks(self):
        "Check closed connection is correctly detected"
        cur=self.db.cursor()
        rowid=cur.execute("create table foo(x blob); insert into foo values(zeroblob(98765)); select rowid from foo").next()[0]
        blob=self.db.blobopen("main", "foo", "x", rowid, True)
        blob.close()
        nargs=self.blob_nargs
        for func in [x for x in dir(blob) if not x.startswith("__") and not x in ("close",)]:
            args=("one", "two", "three")[:nargs.get(func,0)]
            try:
                getattr(blob, func)(*args)
                self.fail("blob method "+func+" didn't notice that the connection is closed")
            except ValueError: # we issue ValueError to be consistent with file objects
                pass
        
        self.db.close()
        nargs=self.connection_nargs
        for func in [x for x in dir(self.db) if not x.startswith("__") and not x in ("close",)]:
            args=("one", "two", "three")[:nargs.get(func,0)]

            try:
                getattr(self.db, func)(*args)
                self.fail("connection method "+func+" didn't notice that the connection is closed")
            except apsw.ConnectionClosedError:
                pass

        # do the same thing, but for cursor
        nargs=self.cursor_nargs
        for func in [x for x in dir(cur) if not x.startswith("__") and not x in ("close",)]:
            args=("one", "two", "three")[:nargs.get(func,0)]
            try:
                getattr(cur, func)(*args)
                self.fail("cursor method "+func+" didn't notice that the connection is closed")
            except apsw.ConnectionClosedError:
                pass

    def testClosing(self):
        "Verify behaviour of close() functions"
        cur=self.db.cursor()
        cur.execute("select 3;select 4")
        self.assertRaises(apsw.IncompleteExecutionError, cur.close)
        # now force it
        self.assertRaises(TypeError, cur.close, sys)
        self.assertRaises(TypeError, cur.close, 1,2,3)
        cur.close(True)
        l=[self.db.cursor() for i in range(1234)]
        cur=self.db.cursor()
        cur.execute("select 3; select 4; select 5")
        l2=[self.db.cursor() for i in range(1234)]
        self.assertRaises(apsw.IncompleteExecutionError, self.db.close)
        self.assertRaises(TypeError, self.db.close, sys)
        self.assertRaises(TypeError, self.db.close, 1,2,3)
        self.db.close(True) # force it
        self.db.close() # should be fine now
        # coverage - close cursor after closing db
        db=apsw.Connection(":memory:")
        cur=db.cursor()
        db.close()
        cur.close()

    def testLargeObjects(self):
        "Verify handling of large strings/blobs (>2GB) [Python 2.5+, 64 bit platform]"
        if sys.version_info<(2,5):
            return
        import ctypes
        if ctypes.sizeof(ctypes.c_size_t)<8:
            return
        # I use an anonymous area slightly larger than 2GB chunk of memory, but don't touch any of it
        import mmap
        f=mmap.mmap(-1, 2*1024*1024*1024+25000)
        c=self.db.cursor()
        c.execute("create table foo(theblob)")
        self.assertRaises(apsw.TooBigError,  c.execute, "insert into foo values(?)", (buffer(f),))
        c.execute("insert into foo values(?)", ("jkghjk"*1024,))
        b=self.db.blobopen("main", "foo", "theblob", self.db.last_insert_rowid(), True)
        b.read(1)
        self.assertRaises(ValueError, b.write, buffer(f))
        f.close()

    def testErrorCodes(self):
        "Verify setting of result codes on error/exception"
        fname="gunk-errcode-test"
        open(fname, "wb").write("A"*8192)
        db=apsw.Connection(fname)
        cur=db.cursor()
        try:
            cur.execute("select * from sqlite_master")
        except apsw.NotADBError,e:
            self.failUnlessEqual(e.result, apsw.SQLITE_NOTADB);
            self.failUnlessEqual(e.extendedresult&0xff, apsw.SQLITE_NOTADB)
        db.close(True)
        
        try:
            os.remove(fname)
        except:
            pass

    def testLimits(self):
        "Verify setting and getting limits"
        self.assertRaises(TypeError, self.db.limit, "apollo", 11)
        c=self.db.cursor()
        c.execute("create table foo(x)")
        c.execute("insert into foo values(?)", ("x"*1024,))
        old=self.db.limit(apsw.SQLITE_LIMIT_LENGTH)
        self.db.limit(apsw.SQLITE_LIMIT_LENGTH, 1023)
        self.assertRaises(apsw.TooBigError, c.execute, "insert into foo values(?)", ("y"*1024,))
        self.failUnlessEqual(1023, self.db.limit(apsw.SQLITE_LIMIT_LENGTH, 0))
        # bug in sqlite - see http://www.sqlite.org/cvstrac/tktview?tn=3085
        if False:
            c.execute("insert into foo values(?)", ("x"*1024,))
            self.failUnlessEqual(apsw.SQLITE_MAX_LENGTH, self.db.limit(apsw.SQLITE_LIMIT_LENGTH))

    def testConnectionHooks(self):
        "Verify connection hooks"
        del apsw.connection_hooks
        try:
            db=apsw.Connection(":memory:")
        except AttributeError:
            pass
        apsw.connection_hooks=sys # bad type
        try:
            db=apsw.Connection(":memory:")
        except TypeError:
            pass
        apsw.connection_hooks=("a", "tuple", "of", "non-callables")
        try:
            db=apsw.Connection(":memory:")
        except TypeError:
            pass
        apsw.connection_hooks=(dir, lambda x: 1/0)
        try:
            db=apsw.Connection(":memory:")
        except ZeroDivisionError:
            pass
        def delit(db):
            del db
        apsw.connection_hooks=[delit for _ in range(9000)]
        db=apsw.Connection(":memory:")
        db.close()

    def testIssue4(self):
        # http://code.google.com/p/apsw/issues/detail?id=4
        connection = apsw.Connection(":memory:")
        cursor = connection.cursor()

        cursor.execute("CREATE TABLE A_TABLE (ID ABC PRIMARY KEY NOT NULL)")
        try:
            cursor.execute("INSERT INTO A_TABLE VALUES (NULL)")
        except Exception, e:
            assert "A_TABLE.ID" in str(e)
    
        try:
            cursor.execute("INSERT INTO A_TABLE VALUES (?)", (None,))
        except Exception, e:
            assert "A_TABLE.ID" in str(e)

    def testWriteUnraiseable(self):
        "Verify writeunraiseable replacement function"
        def unraise():
            # We cause an unraiseable error to happen by writing to a
            # blob open for reading.  The close method called in the
            # destructor will then also give the error
            db=apsw.Connection(":memory:")
            rowid=db.cursor().execute("create table foo(x); insert into foo values(x'aabbccdd'); select rowid from foo").next()[0]
            b=db.blobopen("main", "foo", "x", rowid, False)
            try:
                b.write("badd")
            except apsw.ReadOnlyError:
                pass
            del db
            del b
            gc.collect()
            
        xx=sys.excepthook
        called=[0]
        def ehook(t,v,tb, called=called):
            called[0]=1
        sys.excepthook=ehook
        unraise()
        self.failUnlessEqual(called[0], 1)
        yy=sys.stderr
        sys.stderr=open("errout.txt", "wt")
        def ehook(blah):
            1/0
        sys.excepthook=ehook
        unraise()
        sys.stderr.close()
        v=open("errout.txt", "rt").read()
        os.remove("errout.txt")
        self.failUnless(len(v))
        sys.excepthook=xx
        sys.stderr=yy

    def testStatementCache(self, scsize=100):
        "Verify statement cache integrity"
        cur=self.db.cursor()
        cur.execute("create table foo(x,y)")
        cur.execute("create index foo_x on foo(x)")
        cur.execute("insert into foo values(1,2)")
        cur.execute("drop index foo_x")
        cur.execute("insert into foo values(1,2)") # cache hit, but needs reprepare
        cur.execute("drop table foo; create table foo(x)")
        #cur.execute("insert into foo values(1,2)") # cache hit, but invalid sql
        cur.executemany("insert into foo values(?)", [[1],[2]])
        # overflow the statement cache
        l=[self.db.cursor().execute("select x from foo") for i in xrange(4000)]
        del l
        for _ in cur.execute("select * from foo"): pass
        db2=apsw.Connection("testdb", statementcachesize=scsize)
        cur2=db2.cursor()
        cur2.execute("create table bar(x,y)")
        for _ in cur.execute("select * from foo"): pass
        db2.close()

    def testStatementCacheZeroSize(self):
        self.db=apsw.Connection("testdb", statementcachesize=-1)
        self.testStatementCache(-1)

    def testZeroBlob(self):
        "Verify handling of zero blobs"
        self.assertRaises(TypeError, apsw.zeroblob)
        self.assertRaises(TypeError, apsw.zeroblob, "foo")
        self.assertRaises(TypeError, apsw.zeroblob, -7)
        self.assertRaises(TypeError, apsw.zeroblob, size=27)
        self.assertRaises(OverflowError, apsw.zeroblob, 4000000000)
        cur=self.db.cursor()
        cur.execute("create table foo(x)")
        cur.execute("insert into foo values(?)", (apsw.zeroblob(27),))
        v=cur.execute("select * from foo").next()[0]
        self.assertEqual(v, buffer("\x00"*27))
        # Make sure inheritance works
        class multi(object):
            def __init__(self): self.foo=3
        class derived(multi,apsw.zeroblob):
            def __init__(self, num):
                multi.__init__(self)
                apsw.zeroblob.__init__(self, num)
        cur.execute("delete from foo; insert into foo values(?)", (derived(28),))
        v=cur.execute("select * from foo").next()[0]
        self.assertEqual(v, buffer("\x00"*28))

    def testBlobIO(self):
        "Verify Blob input/output"
        cur=self.db.cursor()
        rowid=cur.execute("create table foo(x blob); insert into foo values(zeroblob(98765)); select rowid from foo").next()[0]
        self.assertRaises(TypeError, self.db.blobopen, 1)
        self.assertRaises(TypeError, self.db.blobopen, u"main", "foo\xf3")
        if sys.version_info>=(2,4):
            # Bug in python 2.3 gives internal error when complex is
            # passed to PyArg_ParseTuple for Long instead of raising
            # TypeError.  Corrected in 2.4
            self.assertRaises(TypeError, self.db.blobopen, u"main", "foo", "x", complex(-1,-1), True)
        self.assertRaises(TypeError, self.db.blobopen, u"main", "foo", "x", rowid, True, False)
        self.assertRaises(apsw.SQLError, self.db.blobopen, "main", "foo", "x", rowid+27, False)
        self.assertRaises(apsw.SQLError, self.db.blobopen, "foo", "foo" , "x", rowid, False)
        self.assertRaises(apsw.SQLError, self.db.blobopen, "main", "x" , "x", rowid, False)
        self.assertRaises(apsw.SQLError, self.db.blobopen, "main", "foo" , "y", rowid, False)
        blobro=self.db.blobopen("main", "foo", "x", rowid, False)
        # sidebar: check they can't be manually created
        self.assertRaises(TypeError, type(blobro))
        # check vals
        self.assertEqual(blobro.length(), 98765)
        self.assertEqual(blobro.length(), 98765)
        self.failUnlessEqual(blobro.read(0), "")
        for i in xrange(98765):
            x=blobro.read(1)
            self.assertEqual("\x00", x)
        x=blobro.read(10)
        self.assertEqual(x, None)
        blobro.seek(0,1)
        self.assertEqual(blobro.tell(), 98765)
        blobro.seek(0)
        self.assertEqual(blobro.tell(), 0)
        self.failUnlessEqual(len(blobro.read(11119999)), 98765)
        blobro.seek(2222)
        self.assertEqual(blobro.tell(), 2222)
        blobro.seek(0,0)
        self.assertEqual(blobro.tell(), 0)
        self.assertEqual(blobro.read(), "\x00"*98765)
        blobro.seek(-3,2)
        self.assertEqual(blobro.read(), "\x00"*3)
        # check types
        self.assertRaises(TypeError, blobro.read, "foo")
        self.assertRaises(TypeError, blobro.tell, "foo")
        self.assertRaises(TypeError, blobro.seek)
        self.assertRaises(TypeError, blobro.seek, "foo", 1)
        self.assertRaises(TypeError, blobro.seek, 0, 1, 2)
        self.assertRaises(ValueError, blobro.seek, 0, -3)
        self.assertRaises(ValueError, blobro.seek, 0, 3)
        # can't seek before begining or after end of file
        self.assertRaises(ValueError, blobro.seek, -1, 0)
        self.assertRaises(ValueError, blobro.seek, 25, 1)
        self.assertRaises(ValueError, blobro.seek, 25, 2)
        self.assertRaises(ValueError, blobro.seek, 100000, 0)
        self.assertRaises(ValueError, blobro.seek, -100000, 1)
        self.assertRaises(ValueError, blobro.seek, -100000, 2)
        blobro.seek(0,0)
        self.assertRaises(apsw.ReadOnlyError, blobro.write, "kermit was here")
        # you get the error on the close too, and blob is always closed - sqlite ticket #2815
        self.assertRaises(apsw.ReadOnlyError, blobro.close) 
        # check can't work on closed blob
        self.assertRaises(ValueError, blobro.read)
        self.assertRaises(ValueError, blobro.seek, 0, 0)
        self.assertRaises(ValueError, blobro.tell)
        self.assertRaises(ValueError, blobro.write, "abc")
        # write tests
        blobrw=self.db.blobopen("main", "foo", "x", rowid, True)
        self.assertEqual(blobrw.length(), 98765)
        blobrw.write("abcd")
        blobrw.seek(0, 0)
        self.assertEqual(blobrw.read(4), "abcd")
        blobrw.write("efg")
        blobrw.seek(0, 0)
        self.assertEqual(blobrw.read(7), "abcdefg")
        blobrw.seek(50, 0)
        blobrw.write(buffer("hijkl"))
        blobrw.seek(-98765, 2)
        self.assertEqual(blobrw.read(55), "abcdefg"+"\x00"*43+"hijkl")
        self.assertRaises(TypeError, blobrw.write, 12)
        self.assertRaises(TypeError, blobrw.write)
        # try to go beyond end
        self.assertRaises(ValueError, blobrw.write, " "*100000)
        self.assertRaises(TypeError, blobrw.close, "elephant")

    def testBlobReadError(self):
        # Check blob read errors handled correctly.  We use a virtual
        # table to generate the error
        class Source:
            def Create(self, db, modulename, dbname, tablename):
                return "create table foo(b blob)", Table()
            Connect=Create
        class Table:
            def BestIndex(self, *args): return None
            def Open(self): return Cursor()

        class Cursor:
            def __init__(self):
                self.pos=0
            def Filter(self, *args):
                self.pos=0
            def Eof(self):
                return self.pos>0
            def Next(self):
                self.pos+=1
            def Rowid(self):
                return self.pos
            def Close(self):
                pass
            def Column(self, col):
                if col<0: return self.pos
                return "foo"
                1/0
                raise apsw.IOError()
        cur=self.db.cursor()
        self.db.createmodule("ioerror", Source())
        cur.execute("create virtual table ioerror using ioerror()")
        blob=self.db.blobopen("main", "ioerror", "b", 0, False)
        blob.read(1)
    # See http://www.sqlite.org/cvstrac/tktview?tn=3078
    del testBlobReadError

# note that a directory must be specified otherwise $LD_LIBRARY_PATH is used
LOADEXTENSIONFILENAME="./testextension.sqlext"

MEMLEAKITERATIONS=1000
PROFILESTEPS=100000

if __name__=='__main__':

    db=apsw.Connection(":memory:")
    if not getattr(db, "enableloadextension", None):
        del APSW.testLoadExtension
    db.close()
    del db

    if getattr(apsw, "enableloadextension", None) and not os.path.exists(LOADEXTENSIONFILENAME):
        print "Not doing LoadExtension test.  You need to compile the extension first"
        if sys.platform.startswith("darwin"):
            print "  gcc -fPIC -bundle -o "+LOADEXTENSIONFILENAME+" -Isqlite3 testextension.c"
        else:
            print "  gcc -fPIC -shared -o "+LOADEXTENSIONFILENAME+" -Isqlite3 testextension.c"
        del APSW.testLoadExtension

    if os.getenv("APSW_NO_MEMLEAK"):
        # Delete tests that have to deliberately leak memory
        # del APSW.testWriteUnraiseable  (used to but no more)
        pass
        
    v=os.getenv("APSW_TEST_ITERATIONS")
    if v is None:
        unittest.main()
    else:
        # we run all the tests multiple times which has better coverage
        # a larger value for MEMLEAKITERATIONS slows down everything else
        MEMLEAKITERATIONS=5
        PROFILESTEPS=1000
        v=int(v)
        for i in xrange(v):
            print "Iteration",i+1,"of",v
            try:
                unittest.main()
            except SystemExit:
                pass

    # Free up everything possible
    del APSW
    del ThreadRunner
    del randomintegers

    # modules
    del apsw
    del unittest
    del os
    del sys
    del math
    del random
    del time
    del threading
    del Queue
    del traceback

    gc.collect()
    del gc
