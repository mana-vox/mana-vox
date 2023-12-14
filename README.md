# Documentation on the Software MANA-Vox Developed by MANA Community (2022)

<img src="https://github.com/mana-vox/mana-vox/blob/main/assets/logo-mana.svg" width="150px">

# Table of contents

- [Summary](#summary)
- [Context](#i-context) 
- [Algorithm Architecture](#ii-algorithm-architecture)
  - [Module 1 - Source Identification](#a-module-1---source-identification)
  - [Module 2 - Content Analysis](#b-module-2---content-analysis)
  - [Modifications](#c-modifications)
- [DevOps](#iii-devops)
  - [Twitter API](#a-twitter-api)
  - [IBM Cloud Watson Services](#b-ibm-cloud-watson-services)
  - [IBM Cloud Databases](#c-ibm-cloud-databases)
  - [IBM Container Registry](#d-ibm-container-registry)
  - [LogDNA](#e-logdna)
- [User Access](#iv-user-access)
  - [API](#a-api)
  - [Backoffice](#b-backoffice)
- [Improvements](#v-improvements)
  - [Profiles Needed](#a-profiles-needed)
- [Additional Documentation](#vi-additional-documentation)

## Summary

**MANA-Vox** is an algorithm developed by the not-for-profit organization **MANA Community** to collect information from civil society on the environmental impacts of companies. This documentation is intended for all **MANA-Vox users**, whether to gain a better understanding of it or to utilize it in some way. After a brief explanation of its creation, each step of the algorithm is thoroughly discussed, allowing developers to comprehend why it was designed this way. Users can focus on the "User Access" section to learn how to access or qualify data. The document also lists potential future improvements.

## I. Context

The aim of the non-profit organization **MANA Community**, through its **MANA-Vox** algorithm, is to gather information on the environmental impacts of companies. Diverse information is not available in one place, and MANA seeks to gather information from the perspective of civil society, other than that of the company itself. **MANA-Vox** therefore searches the Internet for negative information about companies that are engaged in forest-damaging activities, known as incidents. An incident can be an impact, a mobilization, or a sanction. MANA focuses on impacts, trying to publicize them as soon as they occur or at least sooner.

To ensure that the information gathered by the algorithm is credible, it has been programmed to search only websites and Twitter accounts of credible sources. It then displays news from these credible sources on the negative environmental impacts on forests caused by a selected group of companies, primarily in a group of priority countries.

The **MANA-Vox** algorithm aims to be open-sourced so that it could be used by any non-commercial third party that needs it.

## II. Algorithm Architecture

The current algorithm architecture was developed by the Garage (IBM subsidiary) in 2020. It is an update from the previous architecture developed in 2017 by ??? in Swift language for the architecture and Daniel Pouzada (IBM) for the machine learning part (accessible at [https://github.com/dpouzada/Watson_for_MANA](https://github.com/dpouzada/Watson_for_MANA)), using IBM Watson services in Python.

The entire pipeline is currently written in Python and hosted on IBMCloud.

<img src="https://github.com/mana-vox/mana-vox/blob/main/assets/Architecture-Mana.png">

<p align="center">*figure 1: mana-vox architecture*</p>

The algorithm can be separated into two different modules, both executed once per day at 1:00 am and 3:30 am respectively.

### A. Module 1 - Source Identification

The first module is the source identification one. From a pool of reference sources collected manually by **MANA actors**, new sources can be identified. Those reference sources all belong to one of the three main reference groups from which **MANA** built its methodologies: World Wildlife Fund (WWF), Friends Of the Earth (FOE), and Greenpeace (GP). Every time another Twitter account or website is mentioned by those sources in the content they publish, a new entity is created.

A second step is to merge and annotate newly found sources that are similar to existing old sources, to avoid duplication. Only sources with the same Twitter account are automatically merged, the others are only proposed as suitable for merging.

The occurrences of the entities' mentions from one of the references are then numbered: after 3 occurrences, the status of the source switches from ENTITY to SOURCE CANDIDATE, and it will appear in the list of sources that need human validation concerning its credibility. After 10 occurrences from sources appearing in at least two different reference groups, the source will be flagged as 'Trusted'. Only when a source is both trusted and found credible by a human review will its status become SOURCE, meaning it is now a **credible MANA source** whose content will be listened to and analyzed in Module 2. The above steps are summarized in Figure 2.

<img src="https://github.com/mana-vox/mana-vox/blob/main/assets/source-credible.png">

<p align="center">*figure 2: process of making a source credible*</p>

During this part, a twitter profile with its description and its location if any is indicated is stored for each source. When the human review is conducted, the qualifier will from this location enter the source's country of origin (and state if it comes from the United States). The final location can also be a continent or "Global", if the source is operating internationally. If the location is situated in an ecoregion (link: Earth's most special places | WWF), be it entirely or partially, the reviewer should also indicate it, as it will then be listened to in priority. The original goal was to have 60% of prioritary sources, and as of the beginning of 2022, 39% of the verified sources are situated in a region of interest, as defined above.
Other than with this module, sources can also be identified manually through a thoroughly detailed methodology written by Emmanuelle Berenger, accessible at https://mana1.box.com/s/ydxr80jifawrzpos95hkle8gbdwwqz0z (access required), and added through the API (see IV. A).

### B. Module 2 - Content analysis

The second part of the algorithm is tasked with the classification of the content published by the monitored sources. It can be separated into submodules, executed one after the others, as shown in Figure 3.

<img src="https://github.com/mana-vox/mana-vox/blob/main/assets/analyzing-new-content.png">

<p align="center">*figure 3: process of analyzing new content*</p>

The algorithms on IBM Cloud operate by "actions", broken down into a number of 10-minute segments, called "activations", which can be enabled automatically by setting up "triggers". Currently, the content_analysis action is triggered daily at 3:30 am and includes 10 activations of 10 minutes maximum, which is enough to retrieve and analyze all new content published each day. Content published during sequences after all sources have been processed will only be taken into account during the next content analysis action. In the future, it may be more appropriate to run this module more than once a day when the analysis takes more than an hour, as real-time information may be missed.

When new sources are added, it may be necessary to run this action manually, in order to process the greater amount of content available (limited to the last 20 tweets or articles per source - for example for 300 new sources, 5500 new contents had to be processed).

#### 1. Content type

The content type currently retrieved for analysis are the tweets published from the sources through the Twitter API and the RSS feed of the website they might also have. If an article link is present in the retrieved tweet, it will be analyzed instead of the tweet content. Before analysis, the mimetype if the content is checked, and only texts (html/text) or PDFs (application/pdf) are processed. As only 300 calls per 15 minutes are possible using the Twitter API, content retrieval is spread over every other activation until all content is saved, to allow this 15-minute constraint to be exceeded.

#### 2. Scrapping

The scraping of the web contents is realized using the python library Beautifulsoup. To retrieve the articles' content among all the `<p>` balises, a criteria has to be chosen. NLU was originally used to detect keywords in one of those balises, and retrieve the text contained under the parent balise if any match was found. It is now replaced by the company detection submodule: if a monitored company is mentioned anywhere on the webpage, all balise contents under the same parent as the paragraph containing the company will be saved in the database.

#### 3. Translation

Because all articles are not written in English and our model is trained only on English sentences, a text translation module is necessary before analyzing it. Initially done with Watson Language Translator, it should be switched to Google Translate API after a study was performed that showed a bigger language coverage and a better translation accuracy with it (accessible at [this link](https://mana1.box.com/s/c9mu6ridtfxv5kxvqbaklk2mc348h99t) - access required). This translation step is also done before the company detection one, and could be moved after it along with the addition of translated company names (in priority Chinese, Japanese, Arabic, Russian, Thai, Hindi, and Corean) to avoid unnecessary calls, which can be very costly. To reduce translation cost, the following steps may be implemented:

-   Remove texts without companies (this can be done by running company detection before translation, with company names previously translated)
-   Remove texts already written in English (by finding a module that can identify the language, for example)
-   Ensure that a text is not translated twice (which is the case when a text reappears several days in a row, etc.)

A recurring error is the non-translation of content, which occurs when the connection to Watson Language Translator is ineffective. The article is then tagged with -1, with the status "failed_at_translation". If this error occurs too often, for example on a whole day's content, IBM Garage should be contacted to resolve the problem.

#### 4. Company detection

A list of 453 companies of interest was compiled from the three reference NGOs - WWF, Greenpeace, Friend of the Earth. Each item is upstream of its classification by the algorithm analyzed to determine whether any of these companies appear in the text. In this version of the algorithm, this analysis is done by comparing each word to each item in the list. To avoid the non-detection of a company, fuzzy matching is used when the number of characters is greater than 10. One of the main reasons for this choice is that the spelling of a company may vary slightly from one language to another. When the length of the company name does not exceed 10 characters, it must appear accurately in the text. This threshold was chosen after a review of errors in existing companies, the report of which is available [here](https://mana1.box.com/s/c9mu6ridtfxv5kxvqbaklk2mc348h99t) - access required.

Every time a new company is added, a check should be done with the review team afterward to see if this company is often wrongly detected in articles. For example, after a month, check how many mentions there are for each new company, and if one is often mentioned, read some articles related to it to check if there is a problem. If there is, add the name of the company in the list of companies to check more thoroughly (see function “daniel_evaluation,” section “remove problematic companies” of the code). This is directly done for small company names (less than five letters) or small acronyms whose sequence can often be found in other words. Please note that this module is case sensitive. If no company name is detected in the content, the article is labeled with -1, with the status “no_companies”.

#### 5. Analysis

After performing all the above-mentioned steps, a final analysis is performed to classify the article as being a **Oui_MANA** or a **Non_MANA**. The analysis process is the following: each sentence in the text retrieved is sent to Watson Natural Language Understanding (NLU) to detect any interesting keyword. If such a keyword is found, the corresponding sentence is sent to Watson Assistant. Watson Assistant is a personalizable neural network that was in this stance trained with around 200 examples of **Oui_MANA sentences**, and 150 **Non_MANA ones**. It returns a probability for each sentence of interest for each label: the label with the highest probability is assigned to the sentence. If an article or tweet contains at least one **Oui_MANA sentence**, it will be labeled as a 1 (Oui_MANA). Otherwise, it becomes a 0 (Non_MANA).

### C. Modifications

The process of modifying existing code or runs of the algorithm is explained very clearly on the GitHub project page: [IBM Cloud MANA-Vox](https://eu-de.git.cloud.ibm.com/mana-vox/mana-v3) - access required.

#### IBM Garage

The IBM Garage technical team in charge of the development in 2020 and 2021 of **MANA-Vox**, and now available for maintenance, is composed of:

-   Nicolas Comète: [nicolas.comete@fr.ibm.com](mailto:nicolas.comete@fr.ibm.com)
-   Lucile Gramusset: [Lucile.Gramusset1@ibm.com](mailto:Lucile.Gramusset1@ibm.com)

## III. DevOps

The whole solution relies on the following external services:
- Twitter (via Twitter API)
- IBM Cloud Watson services: Watson NLU, NLP, Assistant, and Language Translator
- IBM Cloud Databases ("ICD"): PostgreSQL database
- IBM Container Registry: to store container images
- LogDNA: to centralize logs from jobs and applications

**MANA-Vox** also requires a Knative (Kubernetes serverless environment) called "Code Engine" to execute workloads on IBM Cloud.

**MANA** also has a container on a DigitalOcean server, which is currently used to store the classified sentence dataset for training a potential neural network, and the sentence classification and back-office interfaces. In the long term, the goal would be to migrate **MANA-Vox** and notably the tool's database to this server to become independent of IBM.

### A. Twitter API

A **MANA developer twitter account** is used to connect **MANA-Vox** to the Twitter API and retrieve daily new tweets from followed sources. This account is currently free, with a limit of 300 calls per 15 minutes.

### B. IBM Cloud Watson services

As mentioned above, the content analysis module of the **MANA-Vox algorithm** uses a number of IBM services:

- Watson Natural Language Understanding (NLU), which analyzes text - in this case every sentence of the content - and outputs keywords, as well as a potential location and company if any in the text, with a degree of confidence. It is possible to change the global variable of the algorithm "use_nlu_for_company_detection" to True to detect the company using this NLU function, instead of doing so as described in the step above. However, this method is less efficient and is not used by default.

- Watson Assistant, which is a chatbot assistant that uses a neural network to find out what the best answer is based on the classification - with given classes, and examples for each - of the query. What is used here is the result of this classification, without taking into account the reaction of Watson Assistant afterwards.

- Watson Language Translator, which translates a text given as input. Be careful, the text must not exceed 50 kB, i.e. 50000 characters.

These services are all accessible and configurable from **MANA's IBM Cloud account**.

### C. IBM Cloud Databases

**MANA-Vox** data is stored in a PostgreSQL database on IBM Cloud. The database contains 13 tables:

- entities: contains validated and non-validated sources, with a lot of information, like their status, location, potential ecoregions, etc.
- groups: contains the three reference ONGs - WWF, Greenpeace, Friends of the Earth
- origins: contains the origins from which the content is retrieved, with the type of this origin (twitter, web, rss) and its source, a source being able to have several origins
- origins_mentioned_by_groups: links a content with its origin, source, and source group
- origins_twitter: contains only twitter origins with the screen name of the twitter account and its indicated location
- twitter_profiles: contains more information on the twitter origins, like its description and link
- origins_web: contains only web origins
- origins_rss: contains rss feeds retrieved from the above web origins
- contents: contains the text retrieved or scrapped during the execution of the algorithm, with the link to the tweet or article it comes from, and an "analysis_ts" variable that indicates the time spent analyzing this content. The content analyzed during the next execution is the one for which this variable is null: if a content has to be reanalyzed, for example when an IBM service malfunctions, it is sufficient to reset this variable to its default value NULL
- contents_mentioned_origins: links the content retrieved with its origin
- analysis: contains the results of the analysis of each element of the "contents" table, with one entry per company contained in the text. This table includes, among others, the following columns:
  - metrics on the results of NLU and Watson Assistant
  - the text translated into English
  - the type of text analyzed (text/html or pdf)
  - the final result of the analysis in the "flag" column: 0 if the content is a Non_MANA, 1 if it is a Oui_MANA, and -1 if there was an error or if there is no company in the text
  - a "status" variable which indicates whether the analysis has been "completed", or if not the error that occurred, the most common being "no_companies" or "failed_at_translation"
  - an "expert_MANA" column which allows the results of the manual qualification of the data to be stored
- companies: contains the 453 companies followed
- company_synonyms: contains additional synonyms for the above company list

From this database, data for many tasks can be extracted with the help of SQL queries. As an indication, the query used so far for the extraction of data to be qualified is the following:

```sql
SELECT id, translated_text, "location", company, company_match, link, expert_mana, time_created FROM manav3.analysis
WHERE flag IN (1)
ORDER BY time_created DESC
```

The connections between the different tables are shown in Figure 4. Solid lines connect two tables with a shared key, and dashed lines connect one table (black circle) containing an entry equal to the key of the other table (white circle).

<img src="https://github.com/mana-vox/mana-vox/blob/main/assets/ER-Diagram.png">

<p align="center">*figure 4: database ER diagram*</p>

The schema of this database can be modified directly in the "orm.py" code file, which contains all the classes associated with its tables. When a table or a column in one of these tables is added here, it is also necessary to add it manually in the database (which can be managed with a software like DBeaver for example).

### D. IBM Container Registry

The algorithm is deployed in a container for practicality and adaptability. For more information, here are some links on how containerization or Kubernetes works.

### E. LogDNA

The logs of each execution of the MANA-Vox modules or its API are available on LogDNA (IBM service) and accessible in real time from the IBM MANA account.

## IV. User access

This section concentrates on the tools that a user can use to retrieve or qualify data, without needing a programmer knowledge. An API interface is available, which allows both the connection of other tools to MANA-Vox data and the retrieval of items from the database by a user. A back-office is also under development, which will allow the qualification of the sources and the data analyzed by the algorithm.

### A. API

An API interface was implemented with FastAPI, with the following endpoints:

- `/companies [GET]` 
  - **Description**: Get the current company list
  - **Returns**: list of followed companies (json)

- `/companies [POST]`
  - **Description**: Updates the list of companies
  - **Input**: excel file with two columns
    - “Company_Name”: list of company names to be added
    - “Synonyms”: potential synonyms of the company added, separated with a comma
    <img src="https://github.com/mana-vox/mana-vox/blob/main/assets/companies-post.png">
- `/companies/{name} [DELETE]`
  - **Description**: Deletes a specific company from the list
  - **Input**: company name (string)

- `/sources [POST]`
  - **Description**: Updates the list of sources
  - **Input**: excel file with seven columns
    - “Source”: source name
    - “Groupe”: source group if any (WWF, FOE, GP)
    - “Reference”: TRUE if it is a reference source, FALSE otherwise
    - “Twitter”: name of the Twitter account - it should be unique in the file
    - “Web”: website URL if any - this entry can be empty
    - “Trusted”: TRUE if the source is trusted, FALSE otherwise
    - “Tags”: any additional tags that the user wants to add - for example the name of the person that identified the source
    <img src="https://github.com/mana-vox/mana-vox/blob/main/assets/source-post.png">
  - **Note**: it is best to add the sources in small batches to avoid starting from scratch if an error occurs.

- `/entities/{id1}/merge/{id2} [POST]`
  - **Description**: Merge two entities
  - **Input**: ids (integers) of the entities to merge and merge comment (string)

- `/entities/twitter_location [POST]`
  - **Description**: Get location from Twitter

- `/data [GET]`
  - **Description**: Download data
  - **Input**: start and end date (string($date)) of the data to retrieve
  - **Returns**: list of companies with synonyms, sources, and content analyzed

- `/tests/tweets/ca [POST]`
  - **Description**: Submit tweets for analysis
  - **Input**: tweets (json)

- `/tests/rss/ca [POST]`
  - **Description**: Submit RSS articles for analysis
  - **Input**: articles (json)

- `/tests/rss/extractfeeds [POST]`
  - **Description**: Extract RSS articles from a particular feed
  - **Input**: RSS URL (string) and start date (string($date))

- `/tests/web/extractcontent [POST]`
  - **Description**: Extract content from a website
  - **Input**: website URL (string)

These endpoints are accessible and testable with an admin key on the swagger [http://mana-api.eu-de.mybluemix.net/docs](http://mana-api.eu-de.mybluemix.net/docs) (access required).

If the swagger is not working, a quick troubleshooter is to restart the API (with the "ibmcloud cf restart mana-api" command), and the real-time logs can be accessed using the "ibmcloud cf logs mana-api" command. If the problem persists, it is possible to contact the IBM garage technical team whose contacts are given above.

### B. Backoffice

A back-office is being implemented on [https://github.com/mana-vox/mana-backoffice](https://github.com/mana-vox/mana-backoffice). The goal of this tool is to be able to access, for any user having to qualify or retrieve data, the list of companies, the list of sources, and the list of content classified as Oui_MANA by the algorithm.

#### 1. Configuration

After reviewing the latest technologies, this interface is implemented using the Vue.js 3 framework, Bootstrap 5 for the design, and Axios for the JavaScript HTTP client. The interface aims to be deployed on MANA's DigitalOcean container.

The following presets were selected when creating the interface:

- Vue version 3.x and Vue CLI v4.5.13
- The CSS pre-processor Sass/SCSS (with dart-sass) was chosen
- The Linter/Formatter ESLint + Prettier (with lint on save) was chosen, as it was the one more commonly used
- The history mode was selected for the router
- A few additional features were not selected here, but could all be added later on:
  - TypeScript: extension of JavaScript language that requires more info on data types.
  - Progressive Web App (PWA) Support: for web app on mobile phones
  - Vuex
  - Unit Testing/E2E Testing. This should be added later on.

This configuration was saved in dedicated configuration files.

Bootstrap as well as bootstrap-icons were added as libraries to the project. Bootstrap-vue could not be used here, as it was only developed to work with Vue 2.

#### 2. Components

On this interface, it would be possible with an account to access:

- the list of companies and their synonyms, not editable here
- the list of sources, with their location, the potential ecoregion in which they are located if there is one, whether they have been added by the algorithm or manually, and a variable that validates or not this source after qualification
- the list of contents classified as Oui_MANA by the algorithm and to be qualified by the user, with the link to the content, the source from which it comes, the company involved, the date when the content was retrieved, the result of the qualification to be added, the topic addressed in the text, and the name of the qualifier.

A visual of the back-office design can be found at the link: [https://www.figma.com/file/Vlvtb6YeDdKYG38nDRKhG4/Untitled?node-id=0%3A1](https://www.figma.com/file/Vlvtb6YeDdKYG38nDRKhG4/Untitled?node-id=0%3A1).

Additional features may be added in a later version:

- A filter to give priority to certain geographical origins over others (first depending on whether the source is in an ecoregion, for example, or based on the HDI of the country in which it is located).
- For the same text, with the same URL and the same company, the previous label if it exists should be automatically given. This would save a lot of qualification time.

## V. Improvements

Below is a list of priority improvements that could be made to MANA-Vox:

- Optimization of execution time (to reduce costs and risk of error)
- Finalizing the implementation of the back-office
- Analysis of other types of content: images in tweets or PDF, for example
- Diversification of the source of civil society: the only social network used in this version of the algorithm is Twitter, but it is possible to include Facebook sources (according to IBM Garage)
- Improved machine learning: achieve better results than can be done with the current algorithm (IBM v2) by implementing a MANA-specific classification neural network. This project has already been launched, with a first tool that allows content to be classified sentence by sentence and thus generate samples to form a training dataset. See [https://github.com/mana-vox/mana-classification-tool/tree/main](https://github.com/mana-vox/mana-classification-tool/tree/main).
- Correction of the bias related to the origin of the sources: FOE is mostly French, so many sources are French and do not come from a region of interest
- Analysis of a whole PDF instead of only 50kB
- Include the sentence classification tool in the back-office

Some additional points would also be worth investigating:

- If a source is checked and returned to ENTITY because it has not been validated, it should not be re-marked as a CANDIDATE SOURCE and checked again before becoming a SOURCE.
- Check that the number of occurrences is increased only when a mention is made from a reference source and not from any source.
- Check that adding new companies is possible without replacing the whole list and losing data.
- Check that if a link to a tweet is mentioned in a tweet published by a source, it is this link that will be analyzed.

One last point that would be important to develop - and check if it is legally necessary - is to keep track of what has been written and what MANA is talking about in case articles or tweets are deleted.

### A. Profiles needed

#### 1. Qualifiers

Skills required:
- Fluency in English

Time needed for this position:
The estimated time to process an article after the qualifiers return in 2021 is about 5 minutes per article. As there are currently about 40 articles per day, it would take 4h50min daily to analyze all these articles. One person working 2/3 of the time would, therefore, be sufficient for this position.

#### 2. Developers

**FOR MAINTENANCE ONLY**

Main tasks:
- Export of data for qualification
- Monitoring of potential errors on IBM Cloud and correction of these errors
- Supporting other MANA actors

Skills:
- Proficiency in Python, SQL
- Knowledge of deep learning

Time required:
One to two days per week on average, with some weeks without much work and others where error corrections may require more time.

**FOR MORE DEVELOPMENT**

Main tasks:
- Development of the next tools (back-office, neural network)
- Migration of tools outside IBM
- Integration of tools with potential partners
- Export of data for qualification
- Monitoring for potential errors on IBM Cloud and correcting them
- Supporting other MANA actors

Skills:
- Autonomy
- Proficiency in Python, SQL, and web development (front-end and back-end)
- Proficiency in machine and deep learning
- Knowledge of DevOps operations and containerization would be a plus
- Experience in team management

Time required:
Part-time (much longer development) to full-time (especially if there is team management required).

## VI. Additional documentation

- MANA-Vox v1 documentation (2017): [Mana - Documentation Technique v2](https://mana1.box.com/s/oq72rerkdcj9qt2xqhkxz5mi7te3h1fz)
- IBM Cloud services tutorials: [https://mana1.box.com/s/oq72rerkdcj9qt2xqhkxz5mi7te3h1fz](https://mana1.box.com/s/oq72rerkdcj9qt2xqhkxz5mi7te3h1fz)

