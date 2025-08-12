import asyncio
import requests
import docker
import time
import json
import logging
from typing import Dict, Any

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Test Configuration ---
BASE_URL = "http://localhost:8000/api/v1"
AGENT_SLUG = "clarence-coherence"
CLIENT_ID = "df91fd06-816f-4273-a903-5a4861277040" # Autonomite Client ID
USER_ID = "351bb07b-03fc-4fb4-b09b-748ef8a72084"    # Leandrew Dixon User ID
ROOM_NAME_PREFIX = "perf_test"
SESSION_DURATION_S = 20 # How long to wait for logs after triggering

def get_unique_room_name() -> str:
    """Generates a unique room name for the test run."""
    return f"{ROOM_NAME_PREFIX}_{int(time.time())}"

def print_report(report: Dict[str, Any]):
    """Prints a formatted performance report."""
    logging.info("\n" + "="*80)
    logging.info("PIPELINE PERFORMANCE REPORT")
    logging.info("="*80)

    def print_dict(d: Dict[str, Any], indent: int = 0):
        for key, value in d.items():
            key_str = f"{key.replace('_', ' ').title()}:"
            if isinstance(value, dict):
                logging.info(f"{'  ' * indent}{key_str}")
                print_dict(value, indent + 1)
            elif isinstance(value, float):
                logging.info(f"{'  ' * indent}{key_str:<40} {value:.4f}s")
            else:
                logging.info(f"{'  ' * indent}{key_str:<40} {value}")

    print_dict(report)
    logging.info("="*80 + "\n")


class PerformanceTester:
    def __init__(self):
        self.docker_client = docker.from_env()
        self.room_name = get_unique_room_name()
        self.report = {}
        self.container_names = {
            "fastapi": "sidekick-forge-fastapi",
            "agent_worker": "sidekick-forge_agent-worker_1"
        }

    def find_container(self, service_name_key: str):
        """Finds a container by a partial name match."""
        try:
            # First try the exact name
            return self.docker_client.containers.get(self.container_names[service_name_key])
        except docker.errors.NotFound:
            logging.warning(f"Container '{self.container_names[service_name_key]}' not found. Searching...")
            all_containers = self.docker_client.containers.list()
            for container in all_containers:
                if service_name_key in container.name:
                    logging.info(f"Found matching container: '{container.name}'")
                    # Update name for future lookups
                    self.container_names[service_name_key] = container.name
                    return container
            raise RuntimeError(f"Could not find any running container for service '{service_name_key}'")

    def run_test(self):
        """Orchestrates the entire performance test."""
        try:
            logging.info("--- Phase 1: Restarting Services ---")
            self.restart_services()

            logging.info("--- Phase 2: Triggering Agent ---")
            self.trigger_agent()

            logging.info(f"--- Phase 3: Waiting for {SESSION_DURATION_S}s for agent to process ---")
            time.sleep(SESSION_DURATION_S)

            logging.info("--- Phase 4: Collecting and Analyzing Logs ---")
            self.analyze_logs()

            logging.info("--- Phase 5: Generating Report ---")
            print_report(self.report)

        except Exception as e:
            logging.error(f"An error occurred during the test: {e}", exc_info=True)
        finally:
            logging.info("Test finished.")

    def restart_services(self):
        """Restarts fastapi and agent-worker containers for a clean slate."""
        logging.info("Restarting fastapi service...")
        self.find_container("fastapi").restart()
        logging.info("Restarting agent-worker service...")
        self.find_container("agent_worker").restart()
        logging.info("Waiting for services to initialize...")
        time.sleep(10) # Give them time to come up

    def trigger_agent(self):
        """Makes the API call to trigger the agent."""
        url = f"{BASE_URL}/trigger-agent"
        payload = {
            "agent_slug": AGENT_SLUG,
            "client_id": CLIENT_ID,
            "user_id": USER_ID,
            "room_name": self.room_name,
            "mode": "voice"
        }
        logging.info(f"Sending POST request to {url} with room_name: {self.room_name}")
        response = requests.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
        logging.info("Agent triggered successfully.")
        # We will get the trigger timing from the logs for consistency
        
    def analyze_logs(self):
        """Fetches logs and parses performance data."""
        fastapi_container = self.find_container("fastapi")
        agent_worker_container = self.find_container("agent_worker")

        fastapi_logs = fastapi_container.logs(since=int(time.time()) - 60).decode('utf-8')
        agent_worker_logs = agent_worker_container.logs(since=int(time.time()) - 60).decode('utf-8')

        # Find the logs specific to our test run
        for line in fastapi_logs.splitlines():
            if self.room_name in line and "PERF" in line:
                try:
                    log_data = json.loads(line.split("PERF: ")[1])
                    if log_data.get("event") == "trigger_agent_summary":
                        self.report['api_trigger'] = log_data['details']
                except (json.JSONDecodeError, IndexError):
                    continue

        for line in agent_worker_logs.splitlines():
            if self.room_name in line and "PERF" in line:
                try:
                    log_data = json.loads(line.split("PERF: ")[1])
                    event = log_data.get("event")
                    details = log_data.get("details", {})
                    
                    if event == "agent_job_handler_summary":
                        self.report['agent_worker_initialization'] = details
                    elif event == "context_build_summary":
                        self.report.setdefault('agent_session', {})['context_build'] = details
                    elif event == "perception_to_response_summary":
                         self.report.setdefault('agent_session', {})['first_interaction_latency'] = details

                except (json.JSONDecodeError, IndexError):
                    continue


if __name__ == "__main__":
    tester = PerformanceTester()
    tester.run_test()