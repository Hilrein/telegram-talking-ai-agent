import subprocess
import logging

logger = logging.getLogger(__name__)

def run_terminal_command(command: str) -> str:
    try:
        logger.info(f"Executing terminal command: {command}")
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
        output = result.stdout + "\n" + result.stderr
        if len(output) > 10000:
            output = output[:10000] + "... [TRUNCATED]"
        return output.strip() if output.strip() else "Command executed successfully (no output)."
    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 30 seconds."
    except Exception as e:
        return f"Error executing command: {e}"
