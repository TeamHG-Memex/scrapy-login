import random
from twisted.internet.defer import Deferred
from scrapy.http import Request
from scrapy import log, signals
from scrapy.exceptions import IgnoreRequest


def to_callback(string_or_method_, obj):
    if string_or_method_ is None:
        method = lambda *args, **kwargs: None
    elif isinstance(string_or_method_, basestring):
        method = getattr(obj, string_or_method_)
    else:
        method = string_or_method_
    return method


class LoginError(Exception):
    pass


class LoginMiddleware(object):

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
        if not request.meta.get('login_final_request', False):
            self._enqueue_if_paused(request, spider)

    def process_response(self, request, response, spider):
        if request.meta.get('login_request', False):
            return response
        if request.meta.get('captcha_request', False):
            return response
        if not request.meta.get('login_final_request', False):
            self._enqueue_if_paused(request, spider)
        self.do_login = getattr(spider, 'do_login', None)
        self.check_login = getattr(spider, 'check_login', None)
        self.accounts = getattr(spider, 'accounts', None)
        self.username = getattr(spider, 'username', None)
        self.password = getattr(spider, 'password', None)
        login_callback = getattr(spider, 'login_callback', None)
        self.login_callback = to_callback(login_callback, spider)
        self.dont_resume = getattr(
            spider, 'login_dont_resume', False
        )
        max_attemps = getattr(spider, 'login_max_attemps', self.max_attemps)
        if self.dont_resume and login_callback is None:
            spider.log('You should set login_callback if '
                       'login_dont_resume is set to True, '
                       'otherwise no request is made after login',
                       level=log.WARNING)
        self.spider = spider

        if not all((self.check_login, self.do_login,
                    self.accounts or self.username and self.password)):
            return response
        try:
            login_status = self.check_login(response)
        except LoginError as exc:
            login_successful = False
            login_message = exc.message
        else:
            login_successful = bool(login_status)
            login_message = None

        if login_successful:
            if self.attemp > 0:
                spider.log('Logged in', level=log.INFO)
                self._resume_crawling()
                self.attemp = 0
            return response
        else:
            self._pause_crawling()
            self._enqueue(request, spider)
            if not self.username or not self.password:
                self.username, self.password = random.choice(self.accounts)
            if login_message:
                spider.log('Not logged in: {}'.format(login_message),
                           level=log.WARNING)
            else:
                spider.log('Not logged in', level=log.WARNING)
            self.attemp += 1
            if max_attemps > 0 and self.attemp > max_attemps:
                spider.log('Max login attemps exceeded', level=log.ERROR)
                raise IgnoreRequest('Max login attemps exceeded')
            spider.log('Logging in (attemp {}/{})'
                       .format(self.attemp, max_attemps),
                       level=log.INFO)
            request_or_deferred = self.do_login(response, self.username,
                                                self.password)
            if isinstance(request_or_deferred, Deferred):
                request_or_deferred.addCallbacks(
                    self.deffered_login_callback,
                    self.deffered_login_errback
                )
                raise IgnoreRequest()
            elif isinstance(request_or_deferred, Request):
                request_or_deferred.callback = self.login_callback
                request_or_deferred.meta['login_final_request'] = True
                request_or_deferred.dont_filter = True
                return request_or_deferred
            else:
                raise RuntimeError('do_login must return Request of Deferred')

    def deffered_login_callback(self, request):
        if isinstance(request, Request):
            request.callback = self.login_callback
            request.dont_filter = True
            request.meta['login_final_request'] = True
            self.crawler.engine.crawl(request, self.spider)
        else:
            raise RuntimeError('Deferred has been resolved as non-Request: {}'
                               .format(type(request)))

    def deffered_login_errback(self, failure):
        self.spider.log('Login failed: {}'.format(failure))

    def _pause_crawling(self):
        self.paused = True

    def _resume_crawling(self):
        if not self.paused:
            return
        self.paused = False
        if self.dont_resume:
            self.spider.log('Not resuming crawl')
        else:
            self.spider.log('Resuming crawl: {}'.format(self.queue),
                            level=log.DEBUG)
            for request, spider in self.queue:
                request.dont_filter = True
                self.crawler.engine.crawl(request, spider)

        self.queue[:] = []

    def _enqueue_if_paused(self, request, spider):
        if self.paused:
            self._enqueue(request, spider)
            raise IgnoreRequest('Crawling paused, because login takes a place')

    def _enqueue(self, request, spider):
        self.queue.append((request, spider))
