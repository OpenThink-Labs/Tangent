"""
    Tangent
   Copyright (c) 2013, 2015 David Stalnaker, Richard Zanibbi, Nidhin Pattaniyil,
                  Andrew Kane, Frank Tompa, Kenny Davila Castellanos

    This file is part of Tangent.

    Tanget is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    Tangent is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with Tangent.  If not, see <http://www.gnu.org/licenses/>.

    Contact:
        - Richard Zanibbi: rlaz@cs.rit.edu
"""
from concurrent.futures import ProcessPoolExecutor
import os
from sys import argv
import sys
import codecs
from bs4 import BeautifulSoup
import time
import re

from tangent.math.version03_index import Version03Index
from tangent.math.math_extractor import MathExtractor
from tangent.utility.control import Control
from tangent.utility.Stats import Stats

sys.setrecursionlimit(10000)

"""
    The main application that given an nticr-like query file, queries the collection and returns the results
    Code is based on tangent/ntcir/ntcir11.py
"""


def print_help_and_exit():
    """
    Prints usage statement
    """

    print("Usage: python query.py [<cntl-file>] or python query.py help")
    print("       default <cntl-file> is tangent.cntl")
    print()
    print("where <cntl-file> is a tsv file that contains a list of parameter-value pairs")
    print("and must include at least the following entries:")
    print("     database\\t<directory for storing database files>")
    print("     queries\\t<file with queries in NTCIR format>")
    print("and may optionally include:")
    print("     window\\t<window-size>")
    print("     run\\t<arbitrary name for query run>")
    print("     weights\\t['math_only' | 'math_focused' | 'ntcir_default' | 'math_text_equal']")
    print("         where 'math_only' is default")
    print("     system\\t['Wikipedia' | 'NTCIR Test' | 'NTCIR Actual']")
    print("         where 'Wikipedia' is default")
    print("as well as other pairs.")
    exit()

def process_query_batch(args):
    """
    Given a query, generate query tuples for the math index
    :param args:
    :return: nil
    """
    stats = Stats()
    fileid = os.getpid()

    system, db, run_tag, query_list, topk, math_index, strategy = args
    math_index.openDB(fileid,topk)

    stats.num_documents = len(query_list)

    for (query_num,query_string) in query_list:
        trees = MathExtractor.parse_from_xml(query_string, query_num, stats.missing_tags, stats.problem_files)
        stats.num_expressions += len(trees)

        # also need to handle keyword queries if present
        terms =  re.findall(r"<keyword[^>]*>\s*([^<]*\S)\s*</keyword>",query_string)
        stats.num_keywords += len(terms)

        math_index.search(fileid, query_num, trees, terms, topk)

    math_index.closeDB(fileid)
    return (fileid,stats)


def get_query(query_obj):
    """
    Parse the query object in xml and get the math and text
    :param query_obj:
    :return: query num, doc = '<doc>' formula* keyword* '</doc>'
    """
    query_num = query_obj.num.text.strip().translate({10:r"\n",9:r"\t"})
    query_list = []
    # get formulas
    for f in query_obj.findAll("formula"):
        math = f.find("m:math")  # assumes m is used for namespace
        query_list.append(str(math))

    # get keywords
    for k in query_obj.findAll("keyword"):
        # print("Keyword in query: "+str(k))
        query_list.append(str(k))

    return query_num, "<doc>" + " ".join(query_list) + "</doc>"

if __name__ == '__main__':

    ntcir_main_count = 1000  # main task require 1000 results returned
    ntcir_wiki_count = 100

    if sys.stdout.encoding != 'utf8':
      sys.stdout = codecs.getwriter('utf8')(sys.stdout.buffer, 'strict')
    if sys.stderr.encoding != 'utf8':
      sys.stderr = codecs.getwriter('utf8')(sys.stderr.buffer, 'strict')

    if (len(argv) > 2 or (len(argv) == 2 and argv[1] == 'help')):  # uses control file to control all parameters
        print_help_and_exit()
    else:
        start = time.time()

        try:
            cntl = Control(argv[1]) if len(argv) == 2 else Control()
        except Exception as err:
            print("Error in reading <cntl-file>: " +str(err))
            print_help_and_exit()

        db = cntl.read("database")
        if not db:
            print("<cntl-file> missing database")
            print_help_and_exit()
        query_file = cntl.read("queries")
        if not query_file:
            print("<cntl-file> missing queries")
            print_help_and_exit()
        window = cntl.read("window",num=True)
        if window and window < 1:  # window values smaller than 1 make no sense
            print('Window values smaller than 1 not permitted -- using 1')
            window = 1
        run_tag = cntl.read("run",default="")
        run_tag = 'rit_' + run_tag
        weighting_strategy = cntl.read("weights",default='math_only')
        if weighting_strategy not in ['math_only', 'math_focused' , 'ntcir_default', 'math_text_equal']:
            print("Invalid weighting strategy. Using 'math_only' instead of %s\n" % weighting_strategy)
            weighting_strategy = 'math_only'
        system = cntl.read("system",default='Wikipedia')
        if system not in ['Wikipedia', 'NTCIR Test', 'NTCIR Actual']:
            print("Invalid system. Using 'Wikipedia' instead of %s\n" % system)
            system = 'Wikipedia'

        math_index = Version03Index(cntl, window=window)

        if cntl.read("results"):
            # try ingesting and processing results (temporary setting)
            tuples = math_index.get(query_file)
            for qid,hit in tuples.items():
                print(qid,hit)
        else:


            topk = ntcir_wiki_count if system == 'Wikipedia' else ntcir_main_count


            with open(query_file, encoding='utf-8') as file:
                parsed = BeautifulSoup(file,"lxml")

            query_list = parsed.find_all("topic")
            print("There are %s queries." % (len(query_list)), flush=True)

            combined_stats = Stats()
            fileids = set()

            try:
                query_list_m = list(map(get_query,query_list)) # whole batch for now
                args = [(system, db, run_tag, query_list_m, topk, math_index, weighting_strategy)]

                for p in args:  # single-process execution
                    (fileid,stats) = process_query_batch(p)
                    fileids.add(fileid)
                    combined_stats.add(stats)
            except Exception as err:
                reason = str(err)
                print("Failed to process document "+filename+": "+reason, file=sys.stderr)
                combined_stats.problem_files[reason] = combined_stats.problem_files.get(reason, set())
                combined_stats.problem_files[reason].add(filename)

            cntl.store("query_fileids",str(fileids))

            print("Done preparing query batch for %s against %s" % (query_file, db))
            combined_stats.dump()

            cntl.dump()  # output the revised cntl file

            end = time.time()
            elapsed = end - start

            print("Elapsed time %s" % (elapsed))
