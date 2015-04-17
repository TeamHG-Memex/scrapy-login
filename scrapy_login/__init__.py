from twisted.internet.defer import Deferred
from scrapy.http import Request
from scrapy import log, signals
from scrapy.utils.response import open_in_browser
from scrapy.utils.misc import arg_to_iter
from scrapy.exceptions import IgnoreRequest


def string_or_method(string_or_method_, obj):
    if isinstance(string_or_method_, basestring):
        method = getattr(obj, string_or_method_)
    else:
        method = string_or_method_
    return method


class LoginMiddleware(object):

    original_start_requests = None

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)

    def __init__(self, crawler):
        self.crawler = crawler
        self.queue = []
        self.paused = False
        self.fail_if_not_logged_in = crawler.settings.get(
            'LOGIN_FAIL_IF_NOT_LOGGED_IN', True
        )
        self.max_attemps = crawler.settings.getint('LOGIN_MAX_ATTEMPS', 10)
        self.attemp = 0
        self.debug = crawler.settings.get('LOGIN_DEBUG', False)
        crawler.signals.connect(self._resume_crawling,
                                signal=signals.spider_idle)

    def process_request(self, request, spider):
        if request.meta.get('captcha_request', False):
            return
        if request.meta.get('login_request', False):
            return
        self._enqueue_if_paused(request, spider)

    def process_response(self, request, response, spider):
        if request.meta.get('login_request', False):
            return response
        if request.meta.get('captcha_request', False):
            return response
        self._enqueue_if_paused(request, spider)

        self.do_login = getattr(spider, 'do_login', None)
        self.check_login = getattr(spider, 'check_login', None)
        self.username = getattr(spider, 'username', None)
        self.password = getattr(spider, 'password', None)
        self.login_callback = getattr(spider, 'login_callback', None)
        self.spider = spider

        if not all((self.check_login, self.do_login, self.username,
                    self.password)):
            return response

        if not self.check_login(response):
            self.attemp += 1
            if self.max_attemps > 0 and self.attemp > self.max_attemps:
                spider.log('Max login attemps exceeded', level=log.ERROR)
                raise IgnoreRequest('Max login attemps exceeded')
            spider.log('Logging in (attemp {})'.format(self.attemp),
                       level=log.INFO)
            self._pause_crawling()
            self._enqueue(request, spider)
            request_or_deferred = self.do_login(response, self.username,
                                                self.password)
            if isinstance(request_or_deferred, Deferred):
                request_or_deferred.addCallback(
                    self.deffered_logged_in_callback
                )
                raise IgnoreRequest()
            elif isinstance(request_or_deferred, Request):
                request_or_deferred.callback = self.logged_in_callback
                request_or_deferred.dont_filter = True
                return request_or_deferred
            else:
                raise RuntimeError('do_login must return Request of Deferred')
        else:
            self.attemp = 0
            return response

    def deffered_logged_in_callback(self, request):
        if isinstance(request, Request):
            request.callback = self.logged_in_callback
            self.crawler.engine.crawl(request, self.spider)
        else:
            raise RuntimeError('Deferred has resolved as non-Request: {}'
                               .format(type(request)))

    def logged_in_callback(self, response):
        checked = self.check_login(response)
        if not checked or isinstance(checked, str):
            self.spider.log('Not logged in: {}'.format(checked),
                            level=log.ERROR)
            if self.debug:
                open_in_browser(response)
            if self.fail_if_not_logged_in:
                return
        else:
            open_in_browser(response)
            self.spider.log('Logged in', level=log.INFO)
        if self.login_callback is not None:
            login_callback = string_or_method(self.login_callback,
                                              self.spider)
            self._resume_crawling(destroy_queue=True)
            for r in arg_to_iter(login_callback(response)):
                yield r
        else:
            self._resume_crawling()

    def _pause_crawling(self):
        self.paused = True

    def _resume_crawling(self, destroy_queue=False):
        if not self.paused:
            return
        self.paused = False
        if not destroy_queue:
            self.spider.log('Resuming crawl: {}'.format(self.queue),
                            level=log.DEBUG)
            for request, spider in self.queue:
                request.dont_filter = True
                self.crawler.engine.crawl(request, spider)
        self.queue[:] = []

    def _spider_idle(self):
        from scrapy.exceptions import DontCloseSpider
        raise DontCloseSpider

    def _enqueue_if_paused(self, request, spider):
        if self.paused:
            self._enqueue(request, spider)
            raise IgnoreRequest('Crawling paused, because login takes a place')

    def _enqueue(self, request, spider):
        self.queue.append((request, spider))
