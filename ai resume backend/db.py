import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

logger = logging.getLogger(__name__)

load_dotenv(dotenv_path=Path(__file__).with_name(".env"))

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "resume_builder")

_client: MongoClient = None


def get_client() -> MongoClient:
    global _client
    if _client is None:
        try:
            _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
            _client.admin.command("ping")
            logger.info("Connected to MongoDB at %s", MONGO_URI)
        except ConnectionFailure as e:
            logger.error("Failed to connect to MongoDB: %s", e)
            raise
    return _client


def get_db():
    return get_client()[DB_NAME]


def get_templates_collection():
    return get_db()["templates"]


def get_resumes_collection():
    return get_db()["resumes"]
