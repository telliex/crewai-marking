from pathlib import Path

from crewai import Agent, Crew, LLM, Process, Task
from crewai.project import CrewBase, agent, crew, task
from crewai_tools import DirectoryReadTool, FileReadTool, SerperDevTool
from dotenv import load_dotenv

from awkns_outreach.tools.tier_classifier import classify_tier

load_dotenv()

_INSTRUCTIONS_DIR = Path(__file__).parent / "instructions"

llm = LLM(model="anthropic/claude-sonnet-4-6")


@CrewBase
class OutreachCrew:
    """Runs lead profiling + email draft generation for one lead."""

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    @agent
    def sales_rep_agent(self) -> Agent:
        return Agent(
            config=self.agents_config["sales_rep_agent"],
            tools=[
                SerperDevTool(),
                DirectoryReadTool(directory=str(_INSTRUCTIONS_DIR)),
                FileReadTool(),
            ],
            allow_delegation=False,
            verbose=True,
            llm=llm,
        )

    @agent
    def lead_sales_rep_agent(self) -> Agent:
        return Agent(
            config=self.agents_config["lead_sales_rep_agent"],
            tools=[
                FileReadTool(),
                DirectoryReadTool(directory=str(_INSTRUCTIONS_DIR)),
            ],
            allow_delegation=False,
            verbose=True,
            llm=llm,
        )

    @task
    def lead_profiling_task(self) -> Task:
        return Task(
            config=self.tasks_config["lead_profiling_task"],
            agent=self.sales_rep_agent(),
        )

    @task
    def personalized_outreach_task(self) -> Task:
        return Task(
            config=self.tasks_config["personalized_outreach_task"],
            agent=self.lead_sales_rep_agent(),
            context=[self.lead_profiling_task()],
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )

    def run(self, inputs: dict) -> str:
        """Add tier to inputs and kick off the crew. Returns email draft text."""
        inputs["tier"] = classify_tier(inputs.get("industry", ""))
        inputs["company_name"] = inputs.get("lead_name", "")
        result = self.crew().kickoff(inputs=inputs)
        return str(result)
