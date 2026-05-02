```bash

cp .env.example .env   # run this command and then you need to add the OPENAI_API_KEY the actual one


docker compose --profile dev-mail up --build -d # this will start all the necessary stuff

docker compose run --rm --build api python -m app.seed # this will load user data to the DB

docker compose run --rm --build api python -m app.ingest --batch-size 256 # run this in a seperate terminal, this will take a lot of time, so dont worry

docker compose run --rm --build api python -m app.ingest_policies # run this in a different terminal should take  max 2 mins

# UI   http://localhost:8000 this is your actual UI
# Mail http://localhost:8025 this is where you will see the mail, think of it like your local gmail

#the customer id in the ui is 1
#email is  is demo@atlas.local

#if you dont enter the above values the code will not run.

# command to stop it
docker compose --profile dev-mail down
```
