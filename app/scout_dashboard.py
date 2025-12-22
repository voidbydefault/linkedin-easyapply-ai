import streamlit as st
import pandas as pd
import sqlite3
import os
import sys

# Add project root to path to import database module
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
sys.path.append(PROJECT_ROOT)

from app.bot.database import JobDatabase

st.set_page_config(page_title="Scout Mode Dashboard", layout="wide", page_icon="🔭")

WORK_DIR = os.path.join(PROJECT_ROOT, "work")

def load_scout_data():
    db = JobDatabase(WORK_DIR)
    return db.get_scout_jobs(), db

def toggle_status(db, job_hash, current_status):
    db.toggle_scout_job(job_hash, not current_status)
    st.rerun()

def main():
    st.title("🔭 Scout Mode Results")
    st.markdown("Review jobs found by the Scout Bot. Mark them as 'Completed' once you've reviewed or applied to them manually.")
    
    df, db = load_scout_data()
    
    if df is not None and not df.empty:
        # Metrics
        total_found = len(df)
        completed_count = len(df[df['completed'] == 1])
        pending_count = total_found - completed_count
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Scouted", total_found)
        c2.metric("Pending Review", pending_count)
        c3.metric("Completed", completed_count)
        
        st.markdown("---")
        
        # Filters
        col_filter, col_search = st.columns([1, 2])
        hide_completed = col_filter.checkbox("Hide Completed Jobs", value=True)
        search_query = col_search.text_input("Search (Title or Company)", placeholder="e.g. Python Developer")
        
        # Filtering Logic
        filtered_df = df.copy()
        if hide_completed:
            filtered_df = filtered_df[filtered_df['completed'] == 0]
            
        if search_query:
            filtered_df = filtered_df[
                filtered_df['title'].str.contains(search_query, case=False) |
                filtered_df['company'].str.contains(search_query, case=False)
            ]
            
        # Display as a table with actions
        # Streamlit dataframe with column config
        
        # We need to handle checkboxes interactively
        # Since standard st.dataframe doesn't support direct callback updates easily without experimental_data_editor,
        # we'll iterate for a custom list view or use data_editor if available (Streamlit > 1.23)
        
        # Data Editor approach (Best for "Tracker")
        st.subheader(f"Job List ({len(filtered_df)})")
        
        # Prepare display dataframe
        display_df = filtered_df[['completed', 'score', 'title', 'company', 'location', 'reason', 'url', 'job_hash']].copy()
        
        # UI Configuration
        edited_df = st.data_editor(
            display_df,
            column_config={
                "completed": st.column_config.CheckboxColumn(
                    "Done?",
                    help="Check if you have applied or reviewed this job",
                    default=False,
                ),
                "url": st.column_config.LinkColumn(
                    "Link",
                    display_text="Open Job"
                ),
                "score": st.column_config.ProgressColumn(
                    "Score",
                    format="%d",
                    min_value=0,
                    max_value=100,
                ),
                "job_hash": None # Hide hash
            },
            hide_index=True,
            use_container_width=True,
            key="scout_editor"
        )
        
        # Check for changes -> This relies on the user making edits. 
        # Ideally, we want immediate save, but st.data_editor returns the state.
        # We compare 'edited_df' with 'display_df' to find changes.
        
        # Find differences
        # We only care about 'completed' column changes
        
        if not edited_df.equals(display_df):
            # Identify changed rows
            # We iterate to find which hash changed status
            # This is a bit heavy but robust for small datasets
            
            for index, row in edited_df.iterrows():
                # Find original row by job_hash
                original_row = display_df[display_df['job_hash'] == row['job_hash']].iloc[0]
                
                if row['completed'] != original_row['completed']:
                    # Update DB
                    db.toggle_scout_job(row['job_hash'], row['completed'])
                    st.toast(f"Updated status for: {row['title']}")
                    # Rerun to sync state
                    st.rerun()
                    
    else:
        st.info("No scouted jobs found yet. Run the Scout Bot to populate this list.")
        # Insert Dummy Data Button for Demo
        if st.button("Generate Demo Data (For Testing)"):
            import random
            dummy_jobs = [
                ("https://linkedin.com/job/demo1", "Senior Python Engineer", "TechCorp", "Remote", 95, "Great match for Python and Remote"),
                ("https://linkedin.com/job/demo2", "Data Scientist", "DataAI", "New York", 88, "Strong match for Data Science"),
                ("https://linkedin.com/job/demo3", "Frontend Dev", "WebSolutions", "London", 75, "Decent match but React required"),
                ("https://linkedin.com/job/demo4", "Manager", "BizInc", "Berlin", 60, "Low score due to Experience level")
            ]
            for url, title, company, loc, score, reason in dummy_jobs:
                db.add_scout_job(url, title, company, loc, score, reason)
            st.success("Demo data added! Refreshing...")
            st.rerun()

if __name__ == "__main__":
    main()
