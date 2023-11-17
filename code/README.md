# MANA V3

The MANA-V3 solution is made of 2 main modules each splitted into submodules:
- Source Identification: this module will retrieve and identify new sources
  - Source Acquisition: retrieve new sources from reference sources
  - Source Grouping: merge & annotate newly found sources that are similar
  - Source Tagging: annotate sources that are likely to become trusted / references
- Content Analysis: this module focuses on analyzing content from trusted sources
  - Fetch new content: retrieve latest / refreshed content from sources
  - Analyse content: analyse contents that was not marked as analyzed
  
The whole solution relies on the following external services:

- Twitter (via Twitter API)
- IBM Cloud Watson services: Watson NLU, Assistant and Translator
- IBM Cloud Databases ("ICD"): PostgreSQL database
- IBM Container Registry: to store container images
- LogDNA: to centralize logs from jobs and applications

MANA-V3 also requires a Knative (Kubernetes serverless environment) called "Code Engine" to execute workloads.

## Local setup

### Common setup

- Setup the python virtual environment: 
  ```
  virtualenv venv
  ```
  
- Activate this virtual environment: 
  ```
  source venv/bin/activate
  ```

- Install Python requirements: 
  ```
  pip install -r job-requirements.txt
  pip install -r api-requirements.txt
  ```

- Edit the `env-local-dev` file and update the `DB_CONNECTION_STRING` to suit your development needs (local or remote database)

- Export variables from `env-local-dev` file:
  ```
  export $(grep -v '^#' env-local-dev | xargs)
  ```

- Export `PYTHONPATH`:
  ```
  export PYTHONPATH=src
  ```

### Jobs

#### Source acquisition

Run the source acquisition module:
``` 
python src/source_acquisition/main.py
```
  
#### Source grouping

Run the source grouping module:
``` 
python src/source_grouping/main.py
```
  
#### Source tagging

Run the source tagging module:
``` 
python src/source_tagging/main.py
```

#### Content analysis

**Important note:** for the content analysis module to provide valuable results, the list of companies must be uploaded 
using the API server endpoint.

Run the content analysis module:
``` 
python src/content_analysis/main.py
```

### All jobs

**Important note:** for the content analysis module to provide valuable results, the list of companies must be uploaded 
using the API server endpoint.

Run the job sequence:
``` 
python src/main.py
```

### Backend (API server)

- Start the app :
  ```
  ./venv/bin/uvicorn api.main:app --reload
  ```

- Access the OpenAPI UI with a browser (use the `/docs` base path; e.g. http://localhost:8000/docs)

## IBM Cloud setup

### Common setup

#### Login to IBM Cloud

- Login:

  _If you with to login using user & password:_

  ```
  ibmcloud login -r <REGION> -g <RESOURCE_GROUP> -c <ACCOUNT>
  ```

    e.g.:

  ```
  ibmcloud login -r eu-de -g MANA -c *Account*
  ```

  _If you with to login using API Key:_

  ```
  ibmcloud login -r <REGION> -g <RESOURCE_GROUP> -c <ACCOUNT> --apikey *APIKEY*
  ```

    e.g.:

  ```
  ibmcloud login -r eu-de -g MANA -c meridia.cpl@fr.ibm.com --apikey *APIKEY*
  ```

#### Code Engine (Knative): one-time setup

Code Engine is the runtime environment where all Mana workloads will be executed. Code Engine relies on Knative which
in turn relies on Kubernetes.

- Target the right Code Engine project:
  ```
  ibmcloud ce project select --name mana
  ```

- Create registry secret:
  ```
  ibmcloud ce registry create --name mana-rs --server de.icr.io --username iamapikey --password <api_key> 
  ```

- Create config maps:
  ```
  ibmcloud ce cm create --name watson --from-env-file configmaps-and-secrets/watson-cm
  ibmcloud ce cm create --name api --from-env-file configmaps-and-secrets/api-cm
  ibmcloud ce cm create --name logdna --from-env-file configmaps-and-secrets/logdna-cm

- Create secrets:
  ```
  ibmcloud ce secret create --name watson --from-env-file configmaps-and-secrets/watson-secret
  ibmcloud ce secret create --name twitter --from-env-file configmaps-and-secrets/twitter-secret  
  ibmcloud ce secret create --name api --from-env-file configmaps-and-secrets/api-secret
  ibmcloud ce secret create --name db --from-env-file configmaps-and-secrets/db-secret
  ibmcloud ce secret create --name logdna --from-env-file configmaps-and-secrets/logdna-secret  
  ```

### Source Identification & Content Analysis (SICA) job 
You can build SICA Job using the Cloud shell ( to avoid issue with M1 chip)

- Login to IBM Cloud using command line interface

- Export source acquisition version:
  ``` 
  export SICA_VERSION=0.1.5
  ```

- Build docker image:
  ```
  docker build -t de.icr.io/manavox/sica:${SICA_VERSION} -f job-dockerfile .
  ```

- (Optional) Test your image locally before publishing:
  ```
  docker run -i --env-file env-local-dev de.icr.io/manavox/sica:${SICA_VERSION}
  ```

- Push image to IBM Cloud registry:
  ``` 
  ibmcloud cr login
  docker push de.icr.io/manavox/sica:${SICA_VERSION} 
  ```

- Deploy to Code Engine as a job:
  ```
  ibmcloud ce project select --name mana
  ibmcloud ce job create --name sica \
    --image de.icr.io/manavox/sica:${SICA_VERSION} \
    --env-sec db \
    --env-cm logdna --env-sec logdna \
    --env-sec twitter \
    --env-cm watson --env-sec watson \
    --rs mana-rs \
    --maxexecutiontime 42300
  ```
  Note: for first deployment of the application, use `job create` instead of `job update`.

#### One-time job run

You may run the job for a "one-time" execution:
```
ibmcloud ce project select --name mana
ibmcloud ce jobrun submit --job sica
```
You can access the job status using `ibmcloud ce jr list`

Note: you may "skip" some steps of the job by adding as many `--env <SKIP_STEP>=true` as needed, with the following
`SKIP_STEPS` values:
- `SKIP_SA`: to skip the source acquisition step
- `SKIP_SG`: to skip the source grouping step
- `SKIP_ST`: to skip the source tagging step
- `SKIP_SI`: to skip all source identification steps (SA, SG & ST)
- `SKIP_CA`: to skip the content analysis step
e.g.:
```
ibmcloud ce jobrun submit --job sica --env SKIP_SA=true --env SKIP_CA=true
```

#### Scheduled job run

In order for the SICA job to be run on a regular basis, we need to create a "cron":
``` 
ibmcloud ce project select --name mana
ibmcloud ce sub cron update --name sica-cron \
  --destination-type job \
  --destination sica \
  --schedule '0 3 * * *' \
  --time-zone Europe/Paris
```
In the example above, the job will be triggered every day at 3AM French time.

Note: you may also update your cron by replacing the `create` option with `update`.

### Backend (API server)
You can build the Api application using the Cloud shell ( to avoid issue with M1 chip)

- Set current API version (set the right version for the API):
  ``` 
  export API_VERSION=0.1.0
  ```

- Build the Docker image for Code Engine (use the right value for <version>):
  ```
  docker build -t de.icr.io/manavox/api:${API_VERSION} -f api-dockerfile .
  ```

- (Optional) Test before publishing:
  ```
  docker run -p 8080:8080 --env-file env-local-dev de.icr.io/manavox/api:${API_VERSION}
  ```
  Use the http://localhost:8080/docs to test the API.

- Login to IBM Cloud

- Push the image to the IBM Cloud registry:
  ```
  ibmcloud cr login
  docker push de.icr.io/manavox/api:${API_VERSION}
  ```

- Deploy to Code Engine as an application:
  ```
  ibmcloud ce project select --name mana
  ibmcloud ce app update --name api \
    --image de.icr.io/manavox/api:${API_VERSION} \
    --env-cm api --env-sec api \
    --env-sec db \
    --env-cm logdna --env-sec logdna \
    --env-sec twitter \
    --env-cm watson --env-sec watson \
    --rs mana-rs
  ```
  Note: once the application is deployed, use `app update` instead of `app create`.

- Check API: once application is deployed, you should get its URL:
  ``` 
  ...
  https://api.xxxxx.eu-de.codeengine.appdomain.cloud
  ```
  You should be able to access the API by adding `/docs` at the provided URL.
  Note: first access (or access after inactivity period) might take up to 30 seconds.

### Logs

Real-time logs for all three modules (when running on IBM Cloud) are accessible on LogDNA on IBM Cloud Web Console.

### Updating config map or secret for Code Engine

Whenever a parameter (defined either as a _config map_ or a _secret_) needs updating (e.g. Watson URL
or credentials), here are the steps to follow:
- Update the config map or secret using `ibmcloud ce cm update` or `ibmcloud ce secret update`
- Recreate Code Engine job and/or application
You should be good to go !

### Database backup & restore

#### Backup

Use the following command to back the IBM Cloud database (Postgre):
```
PGPASSWORD=70fb478d07a3fb097de6d01ec0c4f2d916b6ad5607f1346d3cb063ae987d33d8 pg_dump -v -h 830e00f9-ea72-48c4-844e-225187f5869e.ca37437f079js7faako0.databases.appdomain.cloud -p 32193 -U ibm_cloud_a3e612ae_3084_47ef_aaec_de36f8fd3f1b -d ibmclouddb --format plain --schema=manav3 > db.sql
```

#### Restore

- Restore to your _local_ database:
```
PGPASSWORD=p0stgre5 psql --username=ibm-cloud-base-user --file=db.sql --host=localhost --port=5432 --dbname=manadb
```

- Restore to _IBM Cloud_ database:
```
PGPASSWORD=70fb478d07a3fb097de6d01ec0c4f2d916b6ad5607f1346d3cb063ae987d33d8 psql --host=830e00f9-ea72-48c4-844e-225187f5869e.ca37437f079js7faako0.databases.appdomain.cloud --port=32193 --username=ibm_cloud_a3e612ae_3084_47ef_aaec_de36f8fd3f1bd --file=db.sql --dbname=ibmclouddb
```

### Continuous Delivery

#### Pipeline

To be setup

#### Tests

As stated earlier, a framework has been setup for handling tests.<br>
A couple of sample tests have been implemented, but additional tests should be written :
- More unit tests to reach a decent coverage of the solution code
- Include integration tests when an integration env becomes available

**Test framework**

`pytest` has been set up as the main test framework
- All tests are to be placed in the `tests` directory
  - Sample `Unit tests` can be found in `tests`/`unit_tests`
  - Sample `Integration tests` can be found in `tests`/`integration_tests`. But those are **NOT** ran by the pipeline, due to a lack of integration environment.

##### Run Unit tests
In a brand new terminal tab : ( clean from all environment variable export)
- Activate the virtual environment: 
  ```
  source venv/bin/activate
  ```
  
- Export `PYTHONPATH`:
  ```
  export PYTHONPATH=src
  ```
- run test
  ```
  pytest tests/unit_tests/ 
  ```
OR 

- run the test with the covergarde and to create output files result
  ```
  pytest --cov=./ tests/unit_tests/ --junitxml=tests/unit_tests/tests.xml  --cov-report xml
  ```
  
##### As for the **integration tests**, our recommendation is to:

/!\ **The integration test need to be rework** (migrate from cloud function to code engine) **to be run** /!\

- Setup an integration environment
  - Watson services
  - Database
  - Code Engine project
- Write some integration tests in the `tests/integration_tests` directory.
- Add an integration tests stage in the pipeline:
  - **Set up the correct values in the environment variables**, in order to target the integration environment
  - Deploy the job and the api to the matching environment
  - Trigger the integration tests using `pytest` (targeting the specific directory, not to run the unit tests again)
  - If successful, then the pipeline can go on and deploy to the Prod environment
  
Note on running the tests locally: pytest will pickup the environment variables based on the presence of a `.env` file.


### TODO

#### NLU
- add IBM Natural language Understanding in the ressource Group
- set NLU environement variable (configmap & secret, env file etc ...)
- schedule the job run by deploying the cron job 


