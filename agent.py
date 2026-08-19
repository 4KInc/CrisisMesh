"""ADK entry point — exports the root agent for `adk run` and `adk deploy`."""

from src.agents.coordinator.agent import coordinator_agent

root_agent = coordinator_agent
