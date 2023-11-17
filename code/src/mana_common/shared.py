import os
import sys
import twitter
import logging
from logdna import LogDNAHandler
from bs4 import BeautifulSoup
import requests
import re
from watson_developer_cloud import NaturalLanguageUnderstandingV1, AssistantV1, LanguageTranslatorV3
from fuzzysearch import find_near_matches
from tenacity import retry, wait_fixed, stop_after_delay


# Module constants
MIN_DELAY_S = 120
TIMEOUT_REQUESTS_S = 15
TWITTER_RATE_LIMIT_ERROR_CODE = 88
TWITTER_RATE_LIMIT_EXCEED_WAIT_TIME_S = 60
TWITTER_RATE_LIMIT_RETRY_MAX_DELAY_S = 960

# Exportable module variables
log = logging.getLogger()

# Public module variables
is_on_cloud = os.environ.get("__OW_ACTION_NAME") is not None or \
              os.environ.get("CF_INSTANCE_IP") is not None or \
              os.environ.get("CE_DOMAIN") is not None
# Private module variables
_twitter_api = None
_logdna_handler: LogDNAHandler
_natural_language_understanding = None
_assistant = None
_translator = None
_workspace_id_assistant = None
_assistant_user_id = None

COMPANY_LEN_LIMIT_FOR_EXACT_MATCH = 10  # company with len <= will require strict matching - switched to 10 instead of 6, to be validated
MIN_LENGTH_FOR_HTML_AND_PDF_PARAGRAPHS = 50
TRUNCATE_TEXT_LENGTH_FOR_HTML_AND_PDF_PARAGRAPHS = 50000
USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/50.0.2661.102 Safari/537.36'


# ----------------------------------------------------------------------------------------
# /!\ Important: do NOT "print()" outside of Python functions, this will lead to action
#                failure when deployed on IBM Cloud (using Cloud Functions)
# ----------------------------------------------------------------------------------------


def set_logger(app_name):
    global log

    log_format = f"[{app_name}::%(filename)s::%(funcName)s] %(lineno)d %(message)s"

    if log.hasHandlers():
        log.handlers.clear()

    # Use default stdout logger
    log.setLevel(logging.INFO)
    sysout_handler = logging.StreamHandler(sys.stdout)
    sysout_handler.setLevel(logging.INFO)
    formatter = logging.Formatter(log_format)
    sysout_handler.setFormatter(formatter)
    log.addHandler(sysout_handler)

    # Init logs
    if os.environ.get("LOGDNA_URL") is not None and os.environ.get("LOGDNA_KEY") is not None:
        global _logdna_handler
        options = {
            "hostname": "mana-v3",
            "url": os.environ.get("LOGDNA_URL"),
            "app": app_name,
            "include_standard_meta": True
        }
        _logdna_handler = LogDNAHandler(os.environ.get("LOGDNA_KEY"), options)
        _logdna_handler.setFormatter(formatter)
        log.addHandler(_logdna_handler)
        log.info("LogDNA ready")
    else:
        log.info("No LogDNA parameters found, using regular stdout/stderr")


def flush_logs():
    if _logdna_handler is not None:
        _logdna_handler.flush()


def _get_twitter_api():
    global _twitter_api
    if _twitter_api is None:
        # Twitter API
        if os.environ.get("TW_CONSUMER_KEY") is not None \
                and os.environ.get("TW_CONSUMER_SECRET") is not None \
                and os.environ.get("TW_ACCESS_TOKEN_KEY") is not None \
                and os.environ.get("TW_ACCESS_TOKEN_SECRET") is not None:
            _twitter_api = twitter.Api(
                consumer_key=os.environ.get("TW_CONSUMER_KEY"),
                consumer_secret=os.environ.get("TW_CONSUMER_SECRET"),
                access_token_key=os.environ.get("TW_ACCESS_TOKEN_KEY"),
                access_token_secret=os.environ.get("TW_ACCESS_TOKEN_SECRET"),
                tweet_mode='extended'
            )
            log.info("Twitter API set")
    return _twitter_api


def get_assistant():
    global _assistant, _workspace_id_assistant, _assistant_user_id
    if _assistant is None:
        log.info("Setting up Assistant")
        _assistant = AssistantV1(
            version=os.environ.get("WA_VERSION"),
            iam_apikey=os.environ.get("WA_API_KEY"),
            url=os.environ.get("WA_URL")
        )
        _workspace_id_assistant = os.environ.get("WA_WORKSPACE_ID")
        _assistant_user_id = os.environ.get("WA_USER_ID")
        log.info("WA URL: {}".format(os.environ.get("WA_URL")))
        log.info("WA API Key: {}...".format(os.environ.get("WA_API_KEY")[0:5]))

    return _assistant


def get_nlu():
    global _natural_language_understanding
    if _natural_language_understanding is None:
        log.info("Setting up NLU")
        _natural_language_understanding = NaturalLanguageUnderstandingV1(
            version=os.environ.get("WNLU_VERSION"),
            iam_apikey=os.environ.get("WNLU_API_KEY"),
            url=os.environ.get("WNLU_URL")
        )
        log.info("WNLU URL: {}".format(os.environ.get("WNLU_URL")))
        log.info("WNLU API Key: {}...".format(os.environ.get("WNLU_API_KEY")[0:5]))
    return _natural_language_understanding


def get_translator():
    global _translator
    if _translator is None:
        _translator = LanguageTranslatorV3(
            version=os.environ.get("WT_VERSION"),
            iam_apikey=os.environ.get("WT_API_KEY"),
            url=os.environ.get("WT_URL")
        )
        log.info("WT URL: {}".format(os.environ.get("WT_URL")))
        log.info("WT API Key: {}...".format(os.environ.get("WT_API_KEY")[0:5]))
    return _translator


def get_assistant_workspace():
    if _workspace_id_assistant is None:
        get_assistant()
    return _workspace_id_assistant


def get_assistant_user_id():
    if _assistant_user_id is None:
        get_assistant()
    return _assistant_user_id


def get_base_url(url, domains_where_next_element_matters):
    try:
        if any(ext in url for ext in domains_where_next_element_matters):
            return "/".join(url.split("/")[0:4])
        return "/".join(url.split("/")[0:3])
    except:
        return url


# Extract real url from tiny url
def get_full_url(short_url, url_extensions_to_check_for_true_url):
    if short_url is not None and any(ext in short_url for ext in url_extensions_to_check_for_true_url):
        try: 
            print("[get_full_url] trying to get full url for  : " + short_url)
            request_session = requests.Session()
            resp = request_session.head(short_url, allow_redirects=True, timeout=TIMEOUT_REQUESTS_S)
            print("[get_full_url] : " + resp.url)
            return resp.url
        except:
            print("[get_full_url] failed for url " + short_url)
            return short_url
    elif short_url is not None:
        print("[get_full_url] no need to get full url for  : " + short_url)
        return short_url


def find_rss_feed(url):
    log.info("trying to get rss feed in url {}".format(url))
    rss = None
    try:
        response = requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=TIMEOUT_REQUESTS_S)
        soup = BeautifulSoup(response.text, "lxml")
        link = soup.find('link', type='application/rss+xml')
        if link is not None:
            rss = link['href']
            log.info("Found {}".format(rss))
    except Exception as e:
        log.error("exception {}".format(e))
        pass
    return rss


def get_twitter_infos(screen_name, url_extensions_to_check_for_true_url):
    try:
        # get twitter user profile
        user_profile = retryable_twitter_api(
            function_name="GetUser",
            screen_name=screen_name,
            return_json=True
        )
        log.info("user_profile {}".format(user_profile))
    except twitter.error.TwitterError as twitter_ex:
        log.info("Could not retrieve twitter profile for account {}: {}".format(screen_name, twitter_ex))
        return None, None, None

    if user_profile is not None:
        return get_full_url(user_profile['url'], url_extensions_to_check_for_true_url), user_profile['description'], user_profile['location']
    else:
        log.info("Could not retrieve twitter profile for account {}".format(screen_name))
        return None, None, None


def clean_rss_path(rss, base_url):
    if rss is not None:
        if rss.startswith("//"):
            rss = rss.replace("//","http://")
        if not base_url.endswith("/") and not rss.startswith("/"):
            base_url = base_url + "/"
        if not rss.startswith("http"):
            rss = base_url + rss
    return rss


def find_company_name_matches(text, company):
    if len(company) <= COMPANY_LEN_LIMIT_FOR_EXACT_MATCH:
        # For small company names, strict matching
        found = find_near_matches(company, text, max_deletions=0, max_insertions=0, max_substitutions=0,
                                    max_l_dist=0)
    else:
        # Otherwise matching is a little less strict
        found = find_near_matches(company, text, max_deletions=1, max_insertions=1, max_substitutions=0,
                                    max_l_dist=1)
    if len(found) > 0:
        log.info("'{0}' could match".format(company))
        log.info(found)
        result = []
        for f in found:
            result.append({"start" : f.start, "end" : f.end, "dist" : f.dist, "matched" : f.matched })
        return result
    else:
        return None


def build_tweet_url(tweet_id_str, tweet_screen_name):
    return "https://twitter.com/" + tweet_screen_name + "/status/" + tweet_id_str


def find_url_in_text(text):
    urls = re.findall('http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', text)
    log.info("[find_url_in_text] Original string: {}".format(text))
    log.info("[find_url_in_textUrls]: {}".format(urls))
    for i in range(len(urls)):
        url = urls[i]
        if url.endswith(".") or url.endswith(",") or url.endswith(")"):
            urls[i] = url[:-1]
    log.info("[find_url_in_textUrls]: {}".format(urls))
    return urls


def get_redirect_javascript(url):
    max_content_size = 2000000
    # Some web sites don't return code 300 for redirections, but just do it using javascript....
    log.info("[get_redirect_javascript] Trying URL: {}".format(url))
    ret_val = None
    try:
        response = requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=TIMEOUT_REQUESTS_S)
        resp_size = len(response.content)
        if resp_size > max_content_size:
            log.info("[get_redirect_javascript] Content too large for redirect analysis ({} > {}) , skipping".format(resp_size, max_content_size))
        elif resp_size == 0:
            log.info("[get_redirect_javascript] No content, skipping")
        elif response.text is not None:
            soup = BeautifulSoup(response.text, "html.parser")
            if soup.find('head') is not None and soup.find('head').find('script') is not None:
                ret_val = str(soup.find('head').find('script')).split("window.location.href")[1].replace("=", "").replace(
                    "</script>", "").replace('"', "").replace('\n', "").strip()
                log.info("[get_redirect_javascript] Found redirection in JS: {}".format(ret_val))
    except Exception as ex:
        log.info("[get_redirect_javascript] Exception encountered ({})".format(ex))

    return ret_val


def clean_text_before_evaluation(text):
    log.info("[clean_text_before_evaluation] input text: {}".format(text))
    text = text.replace("@", "")
    text = text.replace("#", "")
    text = remove_html_tags(text)
    text = text.replace("\n", " ")

    log.info("[clean_content_before_evaluation] output text : " + text)
    return text


def remove_html_tags(text):
    """Remove html tags from a string"""
    clean = re.compile('<.*?>|&.*?;')
    return re.sub(clean, '', text)


# Some urls should not be processed, for example facebook events
def check_if_url_match_pattern(url, patterns):
    for p in patterns:
        regex = re.compile(p)
        match = regex.search(url)
        if match is not None:
            log.info("Url to ignore found:".format(match.group()))
            return True
    return False

def is_twitter_rate_limit(retry_state):
    try:
        exception = retry_state.outcome.exception()
        if type(exception) is twitter.TwitterError:
            error_code = retry_state.outcome.exception().args[0]["code"]
            return error_code == TWITTER_RATE_LIMIT_ERROR_CODE
    except:
        pass
    return False


def log_retry_info(retry_state):
    log.warning("Twitter API hit rate limit, will pause and retry")


@retry(
    retry=is_twitter_rate_limit,
    wait=wait_fixed(TWITTER_RATE_LIMIT_EXCEED_WAIT_TIME_S),
    stop=stop_after_delay(TWITTER_RATE_LIMIT_RETRY_MAX_DELAY_S),
    after=log_retry_info
)
def retryable_twitter_api(function_name, **twitter_args):
    api_method = getattr(_get_twitter_api(), function_name)
    return api_method(**twitter_args)

