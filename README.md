Scrapy-login
===

A middleware that helps implementing login facility for Scrapy spiders

Usage
---

1. Load middleware in settings.py

    SPIDER_MIDDLEWARES = {
        [...],
        'scrapy_login.LoginMiddleware': 200,
    }


2. Implement `do_login(response, username, password)` in your spider class. `response` var is a response from first start request. This method can return `Request` or `Deferred` that resolves to `Request`.

3. Implement `check_login(response)` method in your spider. It has to check `response` after login for login indicators (eg. logout button, elements that are not available without login) and return `True` if login succeed, otherwise `False` or `str` providing error message.

4. Run your spider with arguments `username` and `password`, for example:

    scrapy crawl -a username=johndoe -a password=mysecret dmoz.com
