
from typing import List
from api.dto import ContentDto, AnalysisDto, RssFeedUrlDto
from mana_common.orm import Content, ContentType
from content_analysis.main import analyse_content, load_cache_companies, extract_rss_content, retrieve_content_from_url
from mana_common.shared import log

def analyse_test_contents(contents: List[ContentDto], content_type: ContentType):
    log.info("Starting for {} test contents".format(len(contents)))
    results = []
    for contentDto in contents:
        analysesDto = []
        log.info("Submitted text : {} ".format(contentDto.text))
        content = Content(content_type=content_type, value=contentDto.text)
        log.info("Loading cache companies to make sure we have the latest version")
        load_cache_companies()
        analysis = analyse_content(content)
        log.info("Returned {} analysis".format(len(analysis)))
        for a in analysis:
            company = a.company if a.company else "NO_COMPANY"
            company_match = str(a.company_match) if a.company_match else "NO_MATCH"
            mana_assistant_result = "NOT_EVALUATED"
            if a.flag == 0:
                mana_assistant_result = "NON_MANA" 
            elif a.flag == 1:
                mana_assistant_result = "OUI_MANA"

            mana_assistant_score = a.mana_assistant_score if a.mana_assistant_score else 0

            analysesDto.append(
                AnalysisDto(
                    company=company,
                    company_match=company_match,
                    original_text=a.original_text,
                    original_language=a.original_language,
                    translated_text=a.translated_text,
                    mana_assistant_result=mana_assistant_result,
                    mana_assistant_score=mana_assistant_score,
                    status=str(a.status),
                    status_exception=str(a.status_exception)
                )
            )

        results.append( { "content" : contentDto , "analysis" : analysesDto })
    return results

def extract_rss_articles(rss_feed: RssFeedUrlDto):
    log.info("Starting for {} test rss_feed".format(rss_feed))
    return extract_rss_content(rss_url=rss_feed.rss_url, since=rss_feed.since.timestamp())

def extract_content_from_url(url):
    return retrieve_content_from_url(url)