"""ADK entry point — exports the root agent for `adk run` and `adk deploy`."""

from src.agents.coordinator.agent import coordinator_agent
from src.core.knowledge_base import init_knowledge_base
from src.core.memory_bank import init_memory_bank

# Load organizational data and pre-seeded lessons
init_knowledge_base()
init_memory_bank()

root_agent = coordinator_agent
