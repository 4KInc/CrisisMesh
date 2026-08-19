"""ADK entry point — exports the root agent for `adk run` and `adk deploy`."""

from src.agents.coordinator.agent import coordinator_agent
from src.core.knowledge_base import init_knowledge_base

# Load organizational data from CSVs into the in-memory knowledge base
init_knowledge_base()

root_agent = coordinator_agent
