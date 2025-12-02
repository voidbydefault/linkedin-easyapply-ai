import sys
import os
from streamlit.web import cli as stcli


def main():
    """
    Programmatic entry point to run the Streamlit dashboard.
    This allows running 'python show_dashboard.py' instead of 'streamlit run dashboard.py'.
    """
    # specific path to the dashboard script
    script_path = os.path.join(os.path.dirname(__file__), "dashboard.py")

    if not os.path.exists(script_path):
        print(f"Error: Could not find dashboard.py at: {script_path}")
        return

    # Construct the argument list mimicking the command line
    sys.argv = ["streamlit", "run", script_path]

    # Run the Streamlit CLI
    sys.exit(stcli.main())

if __name__ == "__main__":
    main()