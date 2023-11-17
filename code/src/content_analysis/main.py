# -*- coding: utf-8 -*-
import re
import io
import time

from mana_common import orm
from mana_common.orm import EntityStatus, Entity, \
    ContentType, Content, Analysis, AnalysisStatus, AnalysisType, \
    get_session, \
    load_cache_companies, find_companies, initialize_analysis, delete_previous_analysis

from watson_developer_cloud.natural_language_understanding_v1 \
    import Features, EntitiesOptions, KeywordsOptions, ConceptsOptions, SentimentOptions, EmotionOptions
import os
from datetime import datetime
from time import mktime
import feedparser
import socket
import requests
from bs4 import BeautifulSoup
import twitter

import fitz  # this is pymupdf

from mana_common.shared import set_logger, log, flush_logs, get_full_url, clean_rss_path, \
    get_nlu, get_assistant_workspace, get_assistant, get_translator, get_assistant_user_id, retryable_twitter_api, \
    build_tweet_url, find_url_in_text, clean_text_before_evaluation, remove_html_tags, \
    MIN_LENGTH_FOR_HTML_AND_PDF_PARAGRAPHS, TRUNCATE_TEXT_LENGTH_FOR_HTML_AND_PDF_PARAGRAPHS, USER_AGENT, \
    TIMEOUT_REQUESTS_S


# Accepted content type to be analyzed
HTML_MIME_TYPES = {'text/html'}
PDF_MIME_TYPES = {'application/pdf'}
RATE_LIMIT_ERROR_CODE = 88
RATE_LIMIT_EXCEED_WAIT_TIME_S = 60


def extract_confidence_score_from_assistant_response(assistant_response, intent_name):
    mana_intent = [intent for intent in assistant_response['intents'] if intent['intent'] == intent_name]
    if len(mana_intent) > 0:
        return mana_intent[0]['confidence']
    return None


def translate(text):
    if len(text) > TRUNCATE_TEXT_LENGTH_FOR_HTML_AND_PDF_PARAGRAPHS:
        log.warning("Text is too long ({}); it will be truncated to {}".format(
            len(text), TRUNCATE_TEXT_LENGTH_FOR_HTML_AND_PDF_PARAGRAPHS)
        )
        text = text[:TRUNCATE_TEXT_LENGTH_FOR_HTML_AND_PDF_PARAGRAPHS]

    language = get_translator().identify(text).get_result()
    probable_language = language['languages'][0]['language']

    confidence = language['languages'][0]['confidence']
    log.info('Identified language: {0} with a confidence of {1}'.format(probable_language, confidence))
    if probable_language != 'en' and confidence >= 0.5:
        translation = get_translator().translate(
            text=text,
            model_id=probable_language + '-en'
        ).get_result()
        text = translation['translations'][0]['translation']
        return probable_language, text

    return probable_language, text


def nlu_analysis(text):
    response_nlu = get_nlu().analyze(
        text=text,
        features=Features(
            concepts=ConceptsOptions(limit=5),
            entities=EntitiesOptions(emotion=True, sentiment=True),
            keywords=KeywordsOptions(emotion=True, sentiment=True),
            sentiment=SentimentOptions(document=True),
            emotion=EmotionOptions(document=True)
        )
    ).get_result()
    return response_nlu


def is_content_relevant_based_on_nlu(text):
    probable_language, translated_text = translate(text)
    response_nlu = nlu_analysis(translated_text)
    try:
        return len(response_nlu["entities"]) > 0 and len(response_nlu["keywords"]) > 0
    except:
        return False


def find_useful_div(p):
    parent = p.find_parent('article')
    if parent is not None:
        return parent

    max_parent_lookup = 5
    current_lookup = 1
    parent = p.parent
    while parent is not None and len(parent.find_all('p')) <= 1 and current_lookup < max_parent_lookup:
        parent = parent.parent
        current_lookup = current_lookup + 1
    return parent


def count_social_keywords(text):
    social_keywords = set(re.findall(orm.get_config()["social_keywords_pattern"], text.lower()))
    return len(social_keywords)


def is_useful_paragraph(p):
    if p.find_parent('header') is not None \
            or p.find_parent('div', {"id": "header"}) is not None \
            or p.find_parent('nav') is not None:
        return False

    if count_social_keywords(p.text) >= 1:
        if p.find_parent('a'):
            return False

    return True


def remove_social_network_sentences(text):
    final_sentences = []
    sentences = text.split(". ")
    for sentence in sentences:
        if count_social_keywords(sentence) <= 1:
            final_sentences.append(sentence)
        else:
            log.info("Removing: {}".format(sentence))
    return ". ".join(final_sentences)


def is_worth_analyzing(text):
    return text is not None and len(text) > MIN_LENGTH_FOR_HTML_AND_PDF_PARAGRAPHS and \
           is_content_relevant_based_on_nlu(text)


def retrieve_content_from_url(url):
    response = requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=TIMEOUT_REQUESTS_S)

    # Retrieve mime-type for the URL
    content_type = response.headers['content-type']
    mime_type = content_type.replace(',', ';').split(';')[0].lower()

    returned_text = ""
    returned_type = None

    if mime_type in HTML_MIME_TYPES:  # URL is an HTML page -> Parse it
        log.info("HTML content found for URL: {}".format(url))
        returned_type = AnalysisType.html
        html_content = response.text
        soup = BeautifulSoup(html_content, "lxml")
        for p in soup.find_all("p"):
            if not (is_useful_paragraph(p)):
                p.parent.decompose()

        useful_div = None

        for p in soup.find_all("p"):
            if is_worth_analyzing(p.text):
                log.info("HTML Content - NLU match found for text: {}".format(p.text))
                useful_div = find_useful_div(p)
                break

        if useful_div is None:
            log.info("HTML Content - Did not find useful div")
        else:
            # text is in original language
            returned_text = " " \
                .join([p.text for p in useful_div.find_all(["p", "h1", "h2"])]).replace("\n", "") \
                .replace("\r", "")

    elif mime_type in PDF_MIME_TYPES:  # URL is a PDF -> Parse it
        log.info("PDF content for URL: {}".format(url))
        returned_type = AnalysisType.pdf
        pdf_file = io.BytesIO(response.content)
        with fitz.open(stream=pdf_file, filetype=mime_type) as doc:
            whole_text = ""
            text_too_large = False
            for page in doc:
                blocks = page.getText("blocks")
                for block in blocks:
                    type = block[6]
                    text = block[4]
                    if type != 0 or text is None:
                        continue  # type != text or no text at all: discard
                    cleansed_text = ' '.join(text.split()).replace("\n", " ").strip()  # remove \n & extra spaces
                    if is_worth_analyzing(cleansed_text):
                        if len(whole_text) + len(cleansed_text) > TRUNCATE_TEXT_LENGTH_FOR_HTML_AND_PDF_PARAGRAPHS:
                            text_too_large = True
                            log.info("PDF Content - Text will be too large, truncating")
                            break
                        whole_text += "\n" + cleansed_text
                if text_too_large:
                    break
            if len(whole_text) > 0:
                log.info("PDF Content - Found valid content for URL: {} ({} chars)".format(url, len(whole_text) - 1))
                returned_text = whole_text[1:]  # remove first \n
            else:
                log.info("PDF Content - Found no valid content for URL: {} ({} chars)".format(url, len(whole_text) - 1))
    else:  # URL is something else (image, video etc...)
        log.info("Content type not supported: {}".format(content_type))

    if len(returned_text) > TRUNCATE_TEXT_LENGTH_FOR_HTML_AND_PDF_PARAGRAPHS:
        log.info("Text is too long ({}); it will be truncated to {}".format(
            len(returned_text), TRUNCATE_TEXT_LENGTH_FOR_HTML_AND_PDF_PARAGRAPHS)
        )

    return returned_text[:TRUNCATE_TEXT_LENGTH_FOR_HTML_AND_PDF_PARAGRAPHS], returned_type


# Process a Twitter origin for a reference entity
def process_twitter_reference(twitter_origin):
    log.info("Processing screen name: {}".format(twitter_origin.screen_name))

    for i in range(5):
        try:
            # get last tweets
            tweets = retryable_twitter_api(
                function_name="GetUserTimeline",
                screen_name=twitter_origin.screen_name,
                since_id=twitter_origin.last_synced_id,
                count=orm.get_config()["max_tweets"]
            )
            break
        except twitter.error.TwitterError as twitter_ex:
            if twitter_ex.code == RATE_LIMIT_ERROR_CODE:
                # If it's a rate limit error, let's wait a bit...
                log.warning(f"Detected 'rate limit' error, will now sleep for {RATE_LIMIT_EXCEED_WAIT_TIME_S}s")
                time.sleep(RATE_LIMIT_EXCEED_WAIT_TIME_S)
                continue
            else:
                log.error("Error while retrieving the latest tweets from the Twitter account: {}".format(twitter_ex))
                # indicate that the account triggered an error - to be dealt with manually in the database
                twitter_origin.valid_extraction = False
                return 0

    log.info('{} has {} new tweet(s)'.format(twitter_origin.screen_name, len(tweets)))

    count = process_tweets(twitter_origin, tweets)

    # Find highest ID for next sync
    max_id = -1
    for t in tweets:
        if t.id > max_id:
            max_id = t.id
    if max_id > 0:
        twitter_origin.last_synced_id = max_id
        get_session().commit()

    return count


def process_entity(entity):
    log.info("Entity {0}".format(entity.name))
    count = 0
    for origin in entity.origins:
        log.info("Origin type {0}".format(origin.type))
        if origin.type == 'twitter_origin' and entity.is_reference is True:
            log.info("Ignoring twitter origin since entity is reference: {0}".format(origin.screen_name))
        elif origin.type == 'twitter_origin' and entity.is_reference is False:
            log.info("Processing twitter origin since entity is NOT reference : {0}".format(origin.screen_name))
            count = process_twitter_reference(origin)
        elif origin.type == 'rss_origin':
            log.info("Processing rss origin {0}".format(origin.rss))
            count = process_rss_reference(origin)
        else:
            log.info("Discarding type: {}".format(origin.type))

    return count


def filter_date(rss_date, last_rss_date):
    return datetime.fromtimestamp(mktime(rss_date)) > datetime.fromtimestamp(last_rss_date)


def extract_rss_content(rss_url, since):
    cleaned_contents = []
    max_rss_ts = None
    try:
        log.info("rss_url {0}".format(rss_url))
        news_feed = feedparser.parse(rss_url).entries
        log.info("Number of RSS posts : {0}".format(len(news_feed)))
        if since is not None:
            news_feed = list(filter(lambda x: filter_date(x.published_parsed, since), news_feed))
            log.info("Number of RSS posts since last retrieval : {0}".format(len(news_feed)))

        for entry in news_feed:
            log.info('Post Title : {}'.format(entry.title))
            cleaned_contents.append({"text": remove_html_tags(entry.summary), "link": entry.link})

        if len(news_feed) > 0:
            max_rss_ts = mktime(news_feed[0].published_parsed)
    except Exception as ex:
        log.warning("EXCEPTION when parsing from {0} : {1}".format(rss_url, ex))
        pass

    return max_rss_ts, cleaned_contents


def process_rss_reference(rss_origin):
    log.info("rss_origin {0}, {1}".format(rss_origin.base_url, rss_origin.rss))
    rss_origin.rss = clean_rss_path(rss_origin.rss, rss_origin.base_url)

    log.info("Will retrieve rss content from {0}".format(rss_origin.rss))
    max_rss_ts, contents = extract_rss_content(rss_origin.rss, rss_origin.last_synced_id)
    count = 0
    for content in contents:
        get_session().add(
            Content(content_type=ContentType.rss, value=content["text"], origin=rss_origin, link=content["link"]))
        count += 1

    if max_rss_ts is not None:
        rss_origin.last_synced_id = max_rss_ts

    get_session().commit()
    return count


# Process a list of tweets
def process_tweets(reference_origin, tweets):
    count = 0
    for t in tweets:
        if t.in_reply_to_status_id is None:
            tweet_content = t.full_text if t.retweeted_status is None else t.retweeted_status.full_text
            if tweet_content:
                save_tweet_content(
                    tweet_content=tweet_content,
                    reference_origin=reference_origin,
                    url=build_tweet_url(t.id_str, t.user.screen_name)
                )
            else:
                log.warning(f"Discarding empty tweet for {reference_origin}")
            count += 1

    get_session().commit()

    return count


# Save the tweet content and where it came from (origin)
def save_tweet_content(tweet_content, reference_origin, url):
    log.info("Saving tweet content produced by : {}".format(reference_origin.entity.name))
    content = Content(value=tweet_content, origin=reference_origin, content_type=ContentType.tweet, link=url)
    get_session().add(content)
    return content


def load_new_content():
    # We want to retrieve :
    # - tweets produced by non reference source (status has to be source, and has to be trusted)
    # - rss produced by non reference source (status has to be source, but does not have to be trusted)
    # !!! BE VERY CAREFUL !!!
    # /!\ Entity.trusted == True is not equivalent to Entity.trusted is True, beware of linter warnings
    # !!!
    query = get_session()\
        .query(Entity) \
        .filter(Entity.status == EntityStatus.SOURCE, Entity.trusted == True)\
        .order_by(Entity.id)
    entities = query.all()
    log.info("Content will be extracted from {} entity(ies)".format(len(entities)))

    count = 1
    length = len(entities)
    total_content = 0
    for e in entities:
        new_content = 0
        try:
            new_content = process_entity(e)
            log.info("Processed entity {} / {}: new content items = {}".format(count, length, new_content))
        except Exception as err:
            log.error("Could not process {} ({})".format(e.name, err))
        total_content += new_content
        count += 1

    log.info("Completed, new content items = {}".format(total_content))


def analyse_contents():
    # !!! BE VERY CAREFUL !!!
    # /!\ Content.analysis_ts == None is not equivalent to Content.analysis_ts is None, beware of linter warnings
    # !!!
    contents = orm.get_session().query(Content).filter(Content.analysis_ts == None).all()

    count = 1
    length = len(contents)
    log.info("Number of contents {}".format(length))
    for c in contents:
        analyses = analyse_content(c)
        for a in analyses:
            log.info("adding analyse to session {}".format(a))
            orm.get_session().add(a)
            orm.get_session().commit()
        log.info("[analyse_contents] Processed content {} / {}".format(count, length))
        count += 1


def send_message_to_assistant(input_text, context):
    # Assistant likes clean text
    cleansed_text = " ".join(input_text.encode("ascii", errors="ignore").decode().split())

    if context is None:
        context = {}

    context["metadata"] = {
        "user_id": get_assistant_user_id()
    }
    log.info(
        "Sending to Watson assistant, text : {}, with user_id {}".format(cleansed_text, context["metadata"]["user_id"]))
    try:
        log.info("And conversation_id = {}".format(context["conversation_id"]))
    except:
        log.info("No conversation_id")
        pass

    return get_assistant().message(
        workspace_id=get_assistant_workspace(),
        input={
            'text': cleansed_text
        },
        context=context
    ).get_result()


def daniel_evaluation(analysis):
    companies = []

    try:
        mana_assistant_score = 0

        text = analysis.original_text
        log.info("Starts")

        try:
            probable_language, translated_text = translate(text)

        except Exception as ex:
            log.info("Exception during translation: {}".format(ex))
            analysis.status = AnalysisStatus.failed_at_translation
            analysis.flag = '-1'
            analysis.status_exception = str(ex)
            return analysis, companies

        analysis.original_language = probable_language
        # This is either the translated text, or original_text. Anyway this is the text used for analysis
        analysis.translated_text = translated_text

        # Removing sentences where there are multiple social network referenced
        translated_text = remove_social_network_sentences(translated_text)

        # Le premier critère est que l'article parle d'une entité "Company"
        company = ""
        location = ""

        company_found = False
        if orm.get_config()["use_nlu_for_company_detection"]:
            log.info("Company detection is made from NLU")

            # On envoie le texte à NLU
            log.info('Submitting to NLU')
            response_nlu = nlu_analysis(translated_text)
            log.info('Got response from NLU')

            i = 0
            while i < len(response_nlu["entities"]) and (company == "" or location == ""):
                if response_nlu["entities"][i]["type"] == "Company" and company == "":
                    company = response_nlu["entities"][i]["text"]
                    log.info("NLU company found: {0}".format(company))
                    nlu_company_confidence = response_nlu["entities"][i]["confidence"]
                    sentiment = response_nlu["entities"][i]["sentiment"]["score"]
                    try:
                        emotion_json_pointer = response_nlu["entities"][i]["emotion"]
                        sadness = emotion_json_pointer["sadness"]
                        joy = emotion_json_pointer["joy"]
                        disgust = emotion_json_pointer["disgust"]
                        anger = emotion_json_pointer["anger"]
                    except:
                        sadness = 0
                        joy = 0
                        disgust = 0
                        anger = 0
                        pass

                    score_pondere_company = -0.5 * (anger + disgust + sadness - joy) + sentiment

                if (response_nlu["entities"][i]["type"] == "Location" and location == ""):
                    location = response_nlu["entities"][i]["text"]
                i += 1

            # On collecte et stocke les valeurs des sentiments et émotions de l'article
            sentiment = response_nlu["sentiment"]["document"]["score"]
            try:
                emotion_json_pointer = response_nlu["emotion"]["document"]["emotion"]
                sadness = emotion_json_pointer["sadness"] if emotion_json_pointer["sadness"] != None else 0
                joy = emotion_json_pointer["joy"] if emotion_json_pointer["joy"] != None else 0
                disgust = emotion_json_pointer["disgust"] if emotion_json_pointer["disgust"] != None else 0
                anger = emotion_json_pointer["anger"] if emotion_json_pointer["anger"] != None else 0
            except:
                sadness = 0
                joy = 0
                disgust = 0
                anger = 0
                pass

            score_pondere = -0.5 * (anger + disgust + sadness - joy) + sentiment
            company_found = (company != "" and score_pondere < 0.5)
        else:
            log.info("Company detection is made from fuzzy matching")
            companies = find_companies(translated_text)  # TODO: switch to non translated text with translated companies

            # remove problematic companies if they don't actually appear in text - add any problematic company name of more than 5 caracters in the list below
            for company_to_verify in companies:
                for detail in company_to_verify[
                    'match_details']:  # if more than one faulty match for the same company, may stop after the first one -- check
                    if (detail['matched'] in ['Nestle', 'Co-op', 'Bailin', 'Clarks', 'Red lantern'] or len(
                            detail['matched']) <= 5):
                        log.info('The mention of {} will be verified'.format(detail['matched']))
                        if not re.compile(r'\b({0})\b'.format(detail['matched'])).search(translated_text):
                            company_to_verify['match_details'].remove(detail)
                            if detail['matched'] in company_to_verify['synonyms']:
                                company_to_verify['synonyms'].remove(detail['matched'])

            for company_to_verify in companies:
                if len(company_to_verify['match_details']) == 0:
                    log.info('No more company match after verification for: {}'.format(company_to_verify))
                    companies.remove(company_to_verify)

            company_found = len(companies) > 0
            score_pondere_company = 100
            nlu_company_confidence = 100

        if company_found:
            if orm.get_config()[
                "use_nlu_for_company_detection"]:  # for non NLU company extraction, we will return the company array and create incidents for each
                analysis.company = company
                analysis.company_match = {"reason": "nlu", "score": nlu_company_confidence}
            else:
                # Sending to NLU
                log.info('Submitting to NLU')
                response_nlu = nlu_analysis(translated_text)
                log.info('Got response from NLU')

                # Getting location
                i = 0
                while i < len(response_nlu["entities"]) and location == "":
                    if response_nlu["entities"][i]["type"] == "Location" and location == "":
                        location = response_nlu["entities"][i]["text"]
                    i += 1

                # On collecte et stocke les valeurs des sentiments et émotions de l'article
                sentiment = response_nlu["sentiment"]["document"]["score"]
                try:
                    emotion_json_pointer = response_nlu["emotion"]["document"]["emotion"]
                    sadness = emotion_json_pointer["sadness"] if emotion_json_pointer["sadness"] is not None else 0
                    joy = emotion_json_pointer["joy"] if emotion_json_pointer["joy"] is not None else 0
                    disgust = emotion_json_pointer["disgust"] if emotion_json_pointer["disgust"] is not None else 0
                    anger = emotion_json_pointer["anger"] if emotion_json_pointer["anger"] is not None else 0
                except:
                    sadness = 0
                    joy = 0
                    disgust = 0
                    anger = 0
                    pass

                score_pondere = -0.5 * (anger + disgust + sadness - joy) + sentiment

            analysis.weighted_score_company = score_pondere_company
            analysis.nlu_company_confidence = nlu_company_confidence

            flag_article_retained = 0
            # We initialize the list of keywords, the dictionary which will store the data on the article after processing and the counter to count how many entities were detected (to further place the article in list_already_treated_MANA_articles by its relevance)
            keywords_list = []
            list_keywords_confirmed = []
            list_alerting_entities_confirmed = []
            list_sentences_confirmed = []
            list_keywords_deceitful = []
            # counter_confirmed_detected_alerting_entities=0

            for l in range(len(response_nlu["keywords"])):
                sentiment = response_nlu["keywords"][l]["sentiment"]["score"]
                try:
                    emotion_json_pointer = response_nlu["keywords"][l]["emotion"]
                    sadness = emotion_json_pointer["sadness"]
                    joy = emotion_json_pointer["joy"]
                    disgust = emotion_json_pointer["disgust"]
                    anger = emotion_json_pointer["anger"]
                except:
                    sadness = 0
                    joy = 0
                    disgust = 0
                    anger = 0
                    pass

                score_pondere_keyword = -0.5 * (anger + disgust + sadness - joy) + sentiment
                keywords_list.append([response_nlu["keywords"][l]["text"], score_pondere_keyword])

            context = None
            for keyword_data in keywords_list:
                keyword = keyword_data[0]
                log.info('Submitting keyword to assistant : {0}'.format(keyword))
                response_bot = send_message_to_assistant(input_text=keyword, context=context)
                log.info(response_bot)
                context = response_bot["context"]
                log.info('Assistant responded : {0}'.format(response_bot["output"]["text"]))
                # If the bot has recognized either an alerting entity or the intent Oui_MANA or Non_MANA then the answer is different that the anything else node with text: 'No redhibitory word detected'
                if response_bot["output"]["text"] != ['No redhibitory word detected']:
                    if response_bot["output"]["text"] != ['OuiMANA'] and response_bot["output"]["text"] != ['NonMANA']:
                        position_alerting_entity = response_bot['entities'][0]['location']
                        alerting_entity = response_bot['input']['text'][
                                          position_alerting_entity[0]:position_alerting_entity[1]]
                        list_alerting_entities_confirmed.append(alerting_entity)
                        # counter_confirmed_detected_alerting_entities+=1
                    for sentence_keyword in translated_text.split('. '):
                        if keyword in sentence_keyword:
                            # If an alerting entity was discovered, meaning it is not one of the intents by elimination
                            # if response_bot["output"]["text"]!=['OuiMANA'] and response_bot["output"]["text"]!=['NonMANA']:
                            # We need the following little trick to catch the exact synonym of entity value that was detected in the input keyword
                            # Having collected the sentences in which this entity appears, we now send them back to the bot, whose nodes were placed with a jump to the nodes of the intents to check whether the sentences trigger the Oui_MANA or Non_MANA intent
                            log.info(
                                'Submitting sentence_keyword (the "trick") to assistant : {0}'.format(sentence_keyword))
                            confirmation_bot = send_message_to_assistant(input_text=sentence_keyword, context=context)
                            log.info('Assistant responded : {0}'.format(confirmation_bot))

                            # The following was trying to add samples automatically to re-train Watson Assistant on the fly. We decided that it's better that the backoffice triggers this
                            if confirmation_bot["output"]["text"] == ['OuiMANA']:
                                # # The value of the flag indicated that the 1st layer detected classified the article, i.e. an alerting entity was detected and its sentences were relevant for MANA
                                # try:
                                #     log.info('assistant adding OuiMANA example : {0}'.format(sentence_keyword))
                                #     assistant.create_example(
                                #         #workspace_id = 'a2dd5d22-63b4-4915-aac8-1c4f6fd358f6',
                                #         workspace_id=workspace_id_assistant,
                                #         intent='OuiMANA',
                                #         text=sentence_keyword,
                                #     ).get_result()
                                # except KeyboardInterrupt:
                                #     return 0
                                # except Exception as ex:
                                #     log.info(ex)
                                #     pass

                                mana_assistant_score = max(mana_assistant_score,
                                                           extract_confidence_score_from_assistant_response(
                                                               confirmation_bot, 'Oui_MANA'))
                                flag_article_retained = 1
                                list_keywords_confirmed.append(keyword_data)
                                list_sentences_confirmed.append(sentence_keyword)

                            elif confirmation_bot["output"]["text"] == ['NonMANA']:
                                # #if response_bot["output"]["text"]!=['OuiMANA'] and response_bot["output"]["text"]!=['NonMANA']:
                                # try:
                                #     log.info('assistant adding NonMANA example : {0}'.format(sentence_keyword))
                                #     assistant.create_example(
                                #         #workspace_id = 'a2dd5d22-63b4-4915-aac8-1c4f6fd358f6',
                                #         workspace_id=workspace_id_assistant,
                                #         intent='NonMANA',
                                #         text=sentence_keyword,
                                #     ).get_result()
                                # except KeyboardInterrupt:
                                #     return 0
                                # except Exception as ex:
                                #     log.info(ex)
                                #     pass
                                list_keywords_deceitful.append(keyword_data)

                            # It is possible that no alerting entity was detected but that the keyword triggered the intent of the bot
                            # Hence it might be a less evident, more subtle MANA phrase with no "redhibitory words", hence the flag value 2 for 2nd layer
                            # (if the flag was not already set to 1 by the confirmation of a MANA alert detection)
                            # else:
                            # confirmation_MANA_sentence(keyword,sentence_keyword,assistant,response_bot,counter_confirmed_detected_alerting_entities,flag_article_retained)

            # if flag_article_retained==0:
            #     classifiers = natural_language_classifier.list_classifiers().get_result()
            #     response_nlc = natural_language_classifier.classify(classifiers["classifiers"][-1]["classifier_id"],text[0:2045]).get_result()
            #     # The flag value of 3 stands for 3rd layer
            #     if response_nlc['top_class']=="Oui_MANA":
            #         flag_article_retained=3

            # If the article was retained by one layer, i.e. that the flag value is not 0, we store all its information
            article_highlighted = translated_text
            if flag_article_retained != 0:
                score_keywords_confirmed = []

                list_sentences_confirmed = list(set(list_sentences_confirmed))
                count_sentences = len(list_sentences_confirmed)
                for sentence in list_sentences_confirmed:
                    article_highlighted = article_highlighted.replace(sentence,
                                                                      '<mark style="background-color: yellow">' + sentence + '</mark>')

                for k in list_keywords_confirmed:
                    score_keywords_confirmed = +k[1]

                list_all_keywords = list_keywords_confirmed + list_keywords_deceitful
                list_all_keywords = list(set(map(tuple, list_all_keywords)))
                for keyword_data in list_all_keywords:
                    article_highlighted = article_highlighted.replace(keyword_data[0],
                                                                      '<mark style="background-color: orange">' +
                                                                      keyword_data[0] + "(" + str(
                                                                          round(keyword_data[1], 2)) + ")" + '</mark>')

                list_alerting_entities_confirmed = list(set(list_alerting_entities_confirmed))
                for keyword in list_alerting_entities_confirmed:
                    article_highlighted = article_highlighted.replace(
                        keyword,
                        '<mark style="background-color: red">' + keyword + '</mark>'
                    )

                article_highlighted = article_highlighted.replace('$', 'dollars')

                analysis.status = AnalysisStatus.completed
                analysis.flag = flag_article_retained
                analysis.mana_assistant_score = mana_assistant_score
                analysis.location = location
                analysis.score = score_pondere
                analysis.count = count_sentences
                analysis.text = article_highlighted
                analysis.score_keywords_confirmed = score_keywords_confirmed
                return analysis, companies

            else:
                list_keywords_deceitful = list(set(map(tuple, list_keywords_deceitful)))
                for keyword_data in list_keywords_deceitful:
                    article_highlighted = article_highlighted.replace(keyword_data[0],
                                                                      '<mark style="background-color: orange">' +
                                                                      keyword_data[0] + "(" + str(
                                                                          round(keyword_data[1], 2)) + ")" + '</mark>')

                analysis.status = AnalysisStatus.completed
                analysis.flag = flag_article_retained
                analysis.location = location
                analysis.score = score_pondere
                analysis.count = 0
                analysis.text = article_highlighted
                analysis.score_keywords_confirmed = 0
                return analysis, companies

        else:
            log.info("No companies found.")
            analysis.status = AnalysisStatus.no_companies
            analysis.flag = '-1'
            return analysis, companies

    except Exception as ex:
        log.warning("Exception during analysis: {}".format(ex))
        analysis.status = AnalysisStatus.other_exception
        analysis.flag = '-1'
        analysis.status_exception = str(ex)
        return analysis, companies


def analyse_content(content):
    log.info('[analyse_content] Starting for content id {}'.format(content.id))
    delete_previous_analysis(content)
    current_analysis = None
    analyses = []
    try:
        if content.content_type == ContentType.tweet:
            log.info("[analyse_content] Starting tweet analysis process")
            at_least_one_valid_url_in_tweet = False
            urls = find_url_in_text(content.value)
            if urls is not None:
                for url in urls:
                    log.info("[analyse_content] found link in content {}".format(url))
                    real_url = get_full_url(url, orm.get_config()["url_extensions_to_check_for_true_url"])
                    log.info(orm.get_config()["url_pattern_to_ignore_for_content_analysis"])
                    if not any(ext in real_url for ext in orm.get_config()["url_pattern_to_ignore_for_content_analysis"]):
                        analysis = initialize_analysis(content)
                        current_analysis = analysis
                        content.analysis_ts = analysis.analysis_ts
                        log.info("[analyse_content] valid real url to analyse {}".format(real_url))
                        analysis.link = real_url

                        analysis.original_text, analysis.type = retrieve_content_from_url(real_url)

                        if len(analysis.original_text) == 0:
                            log.info("[analyse_content] no content retrieved from {}".format(real_url))
                            analysis.status = AnalysisStatus.no_content
                            analyses.append(analysis)
                        else:
                            analysis, companies = daniel_evaluation(analysis)
                            analyses = create_analysis_for_companies(analysis, companies)

                        if analysis.status == AnalysisStatus.failed_at_translation or \
                           analysis.status == AnalysisStatus.other_exception:
                            log.info(
                                "[analyse_content] analysis returned an exception, "
                                "so will try to proceed with the next link"
                            )
                            continue
                        else:
                            at_least_one_valid_url_in_tweet = True

                        if analysis is not None and analysis.flag == '1':
                            log.info(
                                "[analyse_content] analysis returned oui_mana, "
                                "so stopping analysis process after this link"
                            )
                            break
                    else:
                        log.info("[analyse_content] ignoring real url {}".format(real_url))

            log.info("[analyse_content] at_least_one_valid_url_in_tweet {}".format(at_least_one_valid_url_in_tweet))
            if not at_least_one_valid_url_in_tweet:
                log.info("[analyse_content] did not find any valid link in content, so will analyse tweet itself")
                analysis = initialize_analysis(content)
                analysis.type = AnalysisType.tweet
                current_analysis = analysis
                content.analysis_ts = analysis.analysis_ts
                analysis.original_text = clean_text_before_evaluation(content.value)
                analysis, companies = daniel_evaluation(analysis)
                analyses = create_analysis_for_companies(analysis, companies)

        else:
            log.info(
                "[analyse_content] default analysis process for content type : {}".format(content.content_type.name))
            analysis = initialize_analysis(content)
            analysis.type = AnalysisType.tweet
            content.analysis_ts = analysis.analysis_ts
            analysis.original_text = clean_text_before_evaluation(content.value)
            analysis, companies = daniel_evaluation(analysis)
            analyses = create_analysis_for_companies(analysis, companies)

    except Exception as ex:
        log.info("[analyse_content] Exception during analysis: {}".format(ex))
        if current_analysis is not None:
            current_analysis.status = AnalysisStatus.other_exception
            current_analysis.status_exception = str(ex)
            analyses.append(current_analysis)
        pass

    return analyses


def create_analysis_for_companies(analysis, companies):
    log.info("create_analysis_for_companies for {} companies".format(len(companies)))
    analyses = []
    if len(companies) > 0:
        for i in range(len(companies)):
            comp = companies[i]
            log.info("create analysis for {} company".format(comp["company"]))
            if i > 0:
                analysis = Analysis(
                    content=analysis.content,
                    analysis_ts=analysis.analysis_ts,
                    original_text=analysis.original_text,
                    translated_text=analysis.translated_text,
                    original_language=analysis.original_language,
                    flag=analysis.flag,
                    mana_assistant_score=analysis.mana_assistant_score,
                    location=analysis.location,
                    nlu_company_confidence=analysis.nlu_company_confidence,
                    weighted_score_company=analysis.weighted_score_company,
                    score=analysis.score,
                    count=analysis.count,
                    text=analysis.text,
                    score_keywords_confirmed=analysis.score_keywords_confirmed,
                    link=analysis.link,
                    status=analysis.status,
                    type=analysis.type,
                    status_exception=analysis.status_exception)

            analysis.company = comp["company"]
            analysis.company_match = {
                "reason": "fuzzy",
                "synonyms": comp["synonyms"],
                "match_details": comp["match_details"]
            }
            analyses.append(analysis)
    else:
        analyses.append(analysis)
    return analyses


def main():
    socket.setdefaulttimeout(TIMEOUT_REQUESTS_S)

    try:
        set_logger("ca")

        log.info("==Content Analysis==")

        if os.getenv("SKIP_CA"):
            log.info("Skipping step as SKIP_CA is set")
            return

        log.info("Loading companies from DB")
        load_cache_companies()

        log.info("Process entities for new content")
        load_new_content()

        log.info("Analyzing new content")
        analyse_contents()

        log.info("Normal end of processing - completed")
    finally:
        # Flush logs to make sure we've got them all before leaving
        flush_logs()


# Run if local (Docker)
if __name__ == "__main__":
    main()
