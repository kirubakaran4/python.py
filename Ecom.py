
import os
from optparse import OptionGroup
from twisted.python import failure

from scrapy.utils.conf import arglist_to_dict, feed_process_params_from_cli
from scrapy.exceptions import UsageError


class ScrapyCommand:

    requires_project = False
    crawler_process = None

    # default settings to be used for this command instead of global defaults
    default_settings = {}

    exitcode = 0

    def __init__(self):
        self.settings = None  # set in scrapy.cmdline

    def set_crawler(self, crawler):
        if hasattr(self, '_crawler'):
            raise RuntimeError("crawler already set")
        self._crawler = crawler

    def syntax(self):
        """
        Command syntax (preferably one-line). Do not include command name.
        """
        return ""

    def short_desc(self):
        """
        A short description of the command
        """
        return ""

    def long_desc(self):
        """A long description of the command. Return short description when not
        available. It cannot contain newlines, since contents will be formatted
        by optparser which removes newlines and wraps text.
        """
        return self.short_desc()

    def help(self):
        """An extensive help for the command. It will be shown when using the
        "help" command. It can contain newlines, since no post-formatting will
        be applied to its contents.
        """
        return self.long_desc()

    def add_options(self, parser):
        """
        Populate option parse with options available for this command
        """
        group = OptionGroup(parser, "Global Options")
        group.add_option("--logfile", metavar="FILE",
                         help="log file. if omitted stderr will be used")
        group.add_option("-L", "--loglevel", metavar="LEVEL", default=None,
                         help=f"log level (default: {self.settings['LOG_LEVEL']})")
        group.add_option("--nolog", action="store_true",
                         help="disable logging completely")
        group.add_option("--profile", metavar="FILE", default=None,
                         help="write python cProfile stats to FILE")
        group.add_option("--pidfile", metavar="FILE",
                         help="write process ID to FILE")
        group.add_option("-s", "--set", action="append", default=[], metavar="NAME=VALUE",
                         help="set/override setting (may be repeated)")
        group.add_option("--pdb", action="store_true", help="enable pdb on failure")

        parser.add_option_group(group)

    def process_options(self, args, opts):
        try:
            self.settings.setdict(arglist_to_dict(opts.set),
                                  priority='cmdline')
        except ValueError:
            raise UsageError("Invalid -s value, use -s NAME=VALUE", print_help=False)

        if opts.logfile:
            self.settings.set('LOG_ENABLED', True, priority='cmdline')
            self.settings.set('LOG_FILE', opts.logfile, priority='cmdline')

        if opts.loglevel:
            self.settings.set('LOG_ENABLED', True, priority='cmdline')
            self.settings.set('LOG_LEVEL', opts.loglevel, priority='cmdline')

        if opts.nolog:
            self.settings.set('LOG_ENABLED', False, priority='cmdline')

        if opts.pidfile:
            with open(opts.pidfile, "w") as f:
                f.write(str(os.getpid()) + os.linesep)

        if opts.pdb:
            failure.startDebugMode()

    def run(self, args, opts):
        """
        Entry point for running commands
        """
        raise NotImplementedError


class BaseRunSpiderCommand(ScrapyCommand):
    """
    Common class used to share functionality between the crawl, parse and runspider commands
    """
    def add_options(self, parser):
        ScrapyCommand.add_options(self, parser)
        parser.add_option("-a", dest="spargs", action="append", default=[], metavar="NAME=VALUE",
                          help="set spider argument (may be repeated)")
        parser.add_option("-o", "--output", metavar="FILE", action="append",
                          help="append scraped items to the end of FILE (use - for stdout)")
        parser.add_option("-O", "--overwrite-output", metavar="FILE", action="append",
                          help="dump scraped items into FILE, overwriting any existing file")
        parser.add_option("-t", "--output-format", metavar="FORMAT",
                          help="format to use for dumping items")

    def process_options(self, args, opts):
        ScrapyCommand.process_options(self, args, opts)
        try:
            opts.spargs = arglist_to_dict(opts.spargs)
        except ValueError:
            raise UsageError("Invalid -a value, use -a NAME=VALUE", print_help=False)
        if opts.output or opts.overwrite_output:
            feeds = feed_process_params_from_cli(
                self.settings,
                opts.output,
                opts.output_format,
                opts.overwrite_output,
            )
            self.settings.set('FEEDS', feeds, priority='cmdline')
import sys
import time
import subprocess
from urllib.parse import urlencode

import scrapy
from scrapy.commands import ScrapyCommand
from scrapy.linkextractors import LinkExtractor


class Command(ScrapyCommand):

    default_settings = {
        'LOG_LEVEL': 'INFO',
        'LOGSTATS_INTERVAL': 1,
        'CLOSESPIDER_TIMEOUT': 10,
    }

    def short_desc(self):
        return "Run quick benchmark test"

    def run(self, args, opts):
        with _BenchServer():
            self.crawler_process.crawl(_BenchSpider, total=100000)
            self.crawler_process.start()


class _BenchServer:

    def __enter__(self):
        from scrapy.utils.test import get_testenv
        pargs = [sys.executable, '-u', '-m', 'scrapy.utils.benchserver']
        self.proc = subprocess.Popen(pargs, stdout=subprocess.PIPE,
                                     env=get_testenv())
        self.proc.stdout.readline()

    def __exit__(self, exc_type, exc_value, traceback):
        self.proc.kill()
        self.proc.wait()
        time.sleep(0.2)


class _BenchSpider(scrapy.Spider):
    """A spider that follows all links"""
    name = 'follow'
    total = 10000
    show = 20
    baseurl = 'http://localhost:8998'
    link_extractor = LinkExtractor()

    def start_requests(self):
        qargs = {'total': self.total, 'show': self.show}
        url = f'{self.baseurl}?{urlencode(qargs, doseq=1)}'
        return [scrapy.Request(url, dont_filter=True)]

    def parse(self, response):
        for link in self.link_extractor.extract_links(response):
            yield scrapy.Request(link.url, callback=self.parse)
import time
from collections import defaultdict
from unittest import TextTestRunner, TextTestResult as _TextTestResult

from scrapy.commands import ScrapyCommand
from scrapy.contracts import ContractsManager
from scrapy.utils.misc import load_object, set_environ
from scrapy.utils.conf import build_component_list


class TextTestResult(_TextTestResult):
    def printSummary(self, start, stop):
        write = self.stream.write
        writeln = self.stream.writeln

        run = self.testsRun
        plural = "s" if run != 1 else ""

        writeln(self.separator2)
        writeln(f"Ran {run} contract{plural} in {stop - start:.3f}s")
        writeln()

        infos = []
        if not self.wasSuccessful():
            write("FAILED")
            failed, errored = map(len, (self.failures, self.errors))
            if failed:
                infos.append(f"failures={failed}")
            if errored:
                infos.append(f"errors={errored}")
        else:
            write("OK")

        if infos:
            writeln(f" ({', '.join(infos)})")
        else:
            write("\n")


class Command(ScrapyCommand):
    requires_project = True
    default_settings = {'LOG_ENABLED': False}

    def syntax(self):
        return "[options] <spider>"

    def short_desc(self):
        return "Check spider contracts"

    def add_options(self, parser):
        ScrapyCommand.add_options(self, parser)
        parser.add_option("-l", "--list", dest="list", action="store_true",
                          help="only list contracts, without checking them")
        parser.add_option("-v", "--verbose", dest="verbose", default=False, action='store_true',
                          help="print contract tests for all spiders")

    def run(self, args, opts):
        # load contracts
        contracts = build_component_list(self.settings.getwithbase('SPIDER_CONTRACTS'))
        conman = ContractsManager(load_object(c) for c in contracts)
        runner = TextTestRunner(verbosity=2 if opts.verbose else 1)
        result = TextTestResult(runner.stream, runner.descriptions, runner.verbosity)

        # contract requests
        contract_reqs = defaultdict(list)

        spider_loader = self.crawler_process.spider_loader

        with set_environ(SCRAPY_CHECK='true'):
            for spidername in args or spider_loader.list():
                spidercls = spider_loader.load(spidername)
                spidercls.start_requests = lambda s: conman.from_spider(s, result)

                tested_methods = conman.tested_methods_from_spidercls(spidercls)
                if opts.list:
                    for method in tested_methods:
                        contract_reqs[spidercls.name].append(method)
                elif tested_methods:
                    self.crawler_process.crawl(spidercls)

            # start checks
            if opts.list:
                for spider, methods in sorted(contract_reqs.items()):
                    if not methods and not opts.verbose:
                        continue
                    print(spider)
                    for method in sorted(methods):
                        print(f'  * {method}')
            else:
                start = time.time()
                self.crawler_process.start()
                stop = time.time()

                result.printErrors()
                result.printSummary(start, stop)
                self.exitcode = int(not result.wasSuccessful())
from scrapy.commands import BaseRunSpiderCommand
from scrapy.exceptions import UsageError


class Command(BaseRunSpiderCommand):

    requires_project = True

    def syntax(self):
        return "[options] <spider>"

    def short_desc(self):
        return "Run a spider"

    def run(self, args, opts):
        if len(args) < 1:
            raise UsageError()
        elif len(args) > 1:
            raise UsageError("running 'scrapy crawl' with more than one spider is no longer supported")
        spname = args[0]

        crawl_defer = self.crawler_process.crawl(spname, **opts.spargs)

        if getattr(crawl_defer, 'result', None) is not None and issubclass(crawl_defer.result.type, Exception):
            self.exitcode = 1
        else:
            self.crawler_process.start()

            if (
                self.crawler_process.bootstrap_failed
                or hasattr(self.crawler_process, 'has_exception') and self.crawler_process.has_exception
            ):
                self.exitcode = 1
import sys
import os

from scrapy.commands import ScrapyCommand
from scrapy.exceptions import UsageError


class Command(ScrapyCommand):

    requires_project = True
    default_settings = {'LOG_ENABLED': False}

    def syntax(self):
        return "<spider>"

    def short_desc(self):
        return "Edit spider"

    def long_desc(self):
        return ("Edit a spider using the editor defined in the EDITOR environment"
                " variable or else the EDITOR setting")

    def _err(self, msg):
        sys.stderr.write(msg + os.linesep)
        self.exitcode = 1

    def run(self, args, opts):
        if len(args) != 1:
            raise UsageError()

        editor = self.settings['EDITOR']
        try:
            spidercls = self.crawler_process.spider_loader.load(args[0])
        except KeyError:
            return self._err(f"Spider not found: {args[0]}")

        sfile = sys.modules[spidercls.__module__].__file__
        sfile = sfile.replace('.pyc', '.py')
        self.exitcode = os.system(f'{editor} "{sfile}"')
import sys
from w3lib.url import is_url

from scrapy.commands import ScrapyCommand
from scrapy.http import Request
from scrapy.exceptions import UsageError
from scrapy.utils.datatypes import SequenceExclude
from scrapy.utils.spider import spidercls_for_request, DefaultSpider


class Command(ScrapyCommand):

    requires_project = False

    def syntax(self):
        return "[options] <url>"

    def short_desc(self):
        return "Fetch a URL using the Scrapy downloader"

    def long_desc(self):
        return (
            "Fetch a URL using the Scrapy downloader and print its content"
            " to stdout. You may want to use --nolog to disable logging"
        )

    def add_options(self, parser):
        ScrapyCommand.add_options(self, parser)
        parser.add_option("--spider", dest="spider", help="use this spider")
        parser.add_option("--headers", dest="headers", action="store_true",
                          help="print response HTTP headers instead of body")
        parser.add_option("--no-redirect", dest="no_redirect", action="store_true", default=False,
                          help="do not handle HTTP 3xx status codes and print response as-is")

    def _print_headers(self, headers, prefix):
        for key, values in headers.items():
            for value in values:
                self._print_bytes(prefix + b' ' + key + b': ' + value)

    def _print_response(self, response, opts):
        if opts.headers:
            self._print_headers(response.request.headers, b'>')
            print('>')
            self._print_headers(response.headers, b'<')
        else:
            self._print_bytes(response.body)

    def _print_bytes(self, bytes_):
        sys.stdout.buffer.write(bytes_ + b'\n')

    def run(self, args, opts):
        if len(args) != 1 or not is_url(args[0]):
            raise UsageError()
        request = Request(args[0], callback=self._print_response,
                          cb_kwargs={"opts": opts}, dont_filter=True)
        # by default, let the framework handle redirects,
        # i.e. command handles all codes expect 3xx
        if not opts.no_redirect:
            request.meta['handle_httpstatus_list'] = SequenceExclude(range(300, 400))
        else:
            request.meta['handle_httpstatus_all'] = True

        spidercls = DefaultSpider
        spider_loader = self.crawler_process.spider_loader
        if opts.spider:
            spidercls = spider_loader.load(opts.spider)
        else:
            spidercls = spidercls_for_request(spider_loader, request, spidercls)
        self.crawler_process.crawl(spidercls, start_requests=lambda: [request])
        self.crawler_process.start()
import os
import shutil
import string

from importlib import import_module
from os.path import join, dirname, abspath, exists, splitext

import scrapy
from scrapy.commands import ScrapyCommand
from scrapy.utils.template import render_templatefile, string_camelcase
from scrapy.exceptions import UsageError


def sanitize_module_name(module_name):
    """Sanitize the given module name, by replacing dashes and points
    with underscores and prefixing it with a letter if it doesn't start
    with one
    """
    module_name = module_name.replace('-', '_').replace('.', '_')
    if module_name[0] not in string.ascii_letters:
        module_name = "a" + module_name
    return module_name


class Command(ScrapyCommand):

    requires_project = False
    default_settings = {'LOG_ENABLED': False}

    def syntax(self):
        return "[options] <name> <domain>"

    def short_desc(self):
        return "Generate new spider using pre-defined templates"

    def add_options(self, parser):
        ScrapyCommand.add_options(self, parser)
        parser.add_option("-l", "--list", dest="list", action="store_true",
                          help="List available templates")
        parser.add_option("-e", "--edit", dest="edit", action="store_true",
                          help="Edit spider after creating it")
        parser.add_option("-d", "--dump", dest="dump", metavar="TEMPLATE",
                          help="Dump template to standard output")
        parser.add_option("-t", "--template", dest="template", default="basic",
                          help="Uses a custom template.")
        parser.add_option("--force", dest="force", action="store_true",
                          help="If the spider already exists, overwrite it with the template")

    def run(self, args, opts):
        if opts.list:
            self._list_templates()
            return
        if opts.dump:
            template_file = self._find_template(opts.dump)
            if template_file:
                with open(template_file, "r") as f:
                    print(f.read())
            return
        if len(args) != 2:
            raise UsageError()

        name, domain = args[0:2]
        module = sanitize_module_name(name)

        if self.settings.get('BOT_NAME') == module:
            print("Cannot create a spider with the same name as your project")
            return

        if not opts.force and self._spider_exists(name):
            return

        template_file = self._find_template(opts.template)
        if template_file:
            self._genspider(module, name, domain, opts.template, template_file)
            if opts.edit:
                self.exitcode = os.system(f'scrapy edit "{name}"')

    def _genspider(self, module, name, domain, template_name, template_file):
        """Generate the spider module, based on the given template"""
        capitalized_module = ''.join(s.capitalize() for s in module.split('_'))
        tvars = {
            'project_name': self.settings.get('BOT_NAME'),
            'ProjectName': string_camelcase(self.settings.get('BOT_NAME')),
            'module': module,
            'name': name,
            'domain': domain,
            'classname': f'{capitalized_module}Spider'
        }
        if self.settings.get('NEWSPIDER_MODULE'):
            spiders_module = import_module(self.settings['NEWSPIDER_MODULE'])
            spiders_dir = abspath(dirname(spiders_module.__file__))
        else:
            spiders_module = None
            spiders_dir = "."
        spider_file = f"{join(spiders_dir, module)}.py"
        shutil.copyfile(template_file, spider_file)
        render_templatefile(spider_file, **tvars)
        print(f"Created spider {name!r} using template {template_name!r} ",
              end=('' if spiders_module else '\n'))
        if spiders_module:
            print(f"in module:\n  {spiders_module.__name__}.{module}")

    def _find_template(self, template):
        template_file = join(self.templates_dir, f'{template}.tmpl')
        if exists(template_file):
            return template_file
        print(f"Unable to find template: {template}\n")
        print('Use "scrapy genspider --list" to see all available templates.')

    def _list_templates(self):
        print("Available templates:")
        for filename in sorted(os.listdir(self.templates_dir)):
            if filename.endswith('.tmpl'):
                print(f"  {splitext(filename)[0]}")

    def _spider_exists(self, name):
        if not self.settings.get('NEWSPIDER_MODULE'):
            # if run as a standalone command and file with same filename already exists
            if exists(name + ".py"):
                print(f"{abspath(name + '.py')} already exists")
                return True
            return False

        try:
            spidercls = self.crawler_process.spider_loader.load(name)
        except KeyError:
            pass
        else:
            # if spider with same name exists
            print(f"Spider {name!r} already exists in module:")
            print(f"  {spidercls.__module__}")
            return True

        # a file with the same name exists in the target directory
        spiders_module = import_module(self.settings['NEWSPIDER_MODULE'])
        spiders_dir = dirname(spiders_module.__file__)
        spiders_dir_abs = abspath(spiders_dir)
        if exists(join(spiders_dir_abs, name + ".py")):
            print(f"{join(spiders_dir_abs, (name + '.py'))} already exists")
            return True

        return False

    @property
    def templates_dir(self):
        return join(
            self.settings['TEMPLATES_DIR'] or join(scrapy.__path__[0], 'templates'),
            'spiders'
        )
