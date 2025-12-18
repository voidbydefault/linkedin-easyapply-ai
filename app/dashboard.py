import streamlit as st
import pandas as pd
import plotly.express as px
import sqlite3
import os
import yaml
from datetime import datetime

# data sources
# Determine paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "config.yaml")
DEFAULT_WORK_DIR = os.path.join(PROJECT_ROOT, "work")

if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, "r", encoding='utf-8') as f:
        config = yaml.safe_load(f)
        
# Strict 'work' directory usage as per user request
WORK_DIR = os.path.join(PROJECT_ROOT, "work")

DB_PATH = os.path.join(WORK_DIR, "job_history.db")
CSV_PATH = os.path.join(WORK_DIR, "application_log.csv")

st.set_page_config(page_title="LinkedIn Bot Dashboard", layout="wide", page_icon="📊")


# data loader
@st.cache_data(ttl=60)  # cache
def load_data():
    """Loads data from SQLite and CSV, handling missing files/errors."""
    db_df = pd.DataFrame()
    csv_df = pd.DataFrame()

    if os.path.exists(DB_PATH):
        try:
            conn = sqlite3.connect(DB_PATH)
            query = "SELECT job_hash, url, title, status, timestamp FROM jobs"
            db_df = pd.read_sql_query(query, conn)
            conn.close()

            # Convert timestamp to datetime
            db_df['timestamp'] = pd.to_datetime(db_df['timestamp'])
            db_df['date'] = db_df['timestamp'].dt.date
        except Exception as e:
            st.error(f"Error loading Database: {e}")

    if os.path.exists(CSV_PATH):
        try:
            csv_df = pd.read_csv(CSV_PATH)
            csv_df.columns = csv_df.columns.str.strip()

            if 'Timestamp' in csv_df.columns:
                csv_df['Timestamp'] = pd.to_datetime(csv_df['Timestamp'], errors='coerce')
                csv_df['date'] = csv_df['Timestamp'].dt.date
        except Exception as e:
            st.error(f"Error loading CSV: {e}")

    return db_df, csv_df


# dashboard
def main():
    st.title("🤖 LinkedIn Automation Dashboard")

    df_db, df_csv = load_data()

    if df_db.empty and df_csv.empty:
        st.warning(f"No data found in {WORK_DIR}. Please run the bot first.")
        return

    st.subheader("🚀 Overview")
    kpi1, kpi2, kpi3, kpi4 = st.columns(4)

    # calculations
    total_scanned = len(df_db) if not df_db.empty else 0

    # find "Applied"
    if not df_db.empty:
        total_applied = len(df_db[df_db['status'].str.contains('Applied', case=False, na=False)])
    elif not df_csv.empty:
        total_applied = len(df_csv[csv_df['Status'] == 'Applied'])
    else:
        total_applied = 0

    success_rate = (total_applied / total_scanned * 100) if total_scanned > 0 else 0

    # Avg applications per active day
    if not df_db.empty and total_applied > 0:
        active_days = df_db['date'].nunique()
        avg_apps = total_applied / active_days if active_days > 0 else 0
    else:
        avg_apps = 0

    kpi1.metric("Total Jobs Scanned", f"{total_scanned:,}")
    kpi2.metric("Total Applied", f"{total_applied:,}")
    kpi3.metric("Success Rate", f"{success_rate:.2f}%")
    kpi4.metric("Avg Apps / Day", f"{avg_apps:.1f}")

    st.markdown("---")

    # charts: status and timeline
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("📊 Status Breakdown")
        if not df_db.empty:
            status_counts = df_db['status'].value_counts().reset_index()
            status_counts.columns = ['Status', 'Count']

            fig_pie = px.pie(status_counts, values='Count', names='Status',
                             title='Job Processing Outcomes', hole=0.4)
            st.plotly_chart(fig_pie, width="stretch")
        else:
            st.info("No database data for status breakdown.")

    with col2:
        st.subheader("📈 Activity Timeline")
        if not df_db.empty:
            daily_activity = df_db.groupby('date').size().reset_index(name='Count')
            fig_line = px.line(daily_activity, x='date', y='Count',
                               title='Jobs Scanned per Day', markers=True)
            st.plotly_chart(fig_line, width="stretch")
        else:
            st.info("No timeline data available.")

    # charts: geography and job titles
    st.markdown("---")
    col3, col4 = st.columns(2)

    with col3:
        st.subheader("🌍 Top Locations (Applied/Log)")
        if not df_csv.empty and 'Location' in df_csv.columns:
            top_locs = df_csv['Location'].value_counts().head(10).reset_index()
            top_locs.columns = ['Location', 'Count']

            fig_loc = px.bar(top_locs, x='Count', y='Location', orientation='h',
                             title='Top 10 Locations', color='Count')
            fig_loc.update_layout(yaxis={'categoryorder': 'total ascending'})
            st.plotly_chart(fig_loc, width="stretch")
        else:
            st.info("Location data missing (requires application_log.csv).")

    with col4:
        st.subheader("💼 Top Job Titles (Scanned)")
        if not df_db.empty:
            # clean and group
            top_titles = df_db['title'].value_counts().head(10).reset_index()
            top_titles.columns = ['Title', 'Count']

            fig_title = px.bar(top_titles, x='Count', y='Title', orientation='h',
                               title='Top 10 Job Titles Scanned', color='Count')
            fig_title.update_layout(yaxis={'categoryorder': 'total ascending'})
            st.plotly_chart(fig_title, width="stretch")
        else:
            st.info("Title data missing.")

    # table: raw data
    st.markdown("---")
    st.subheader("📄 Raw Log Data")

    # filter control
    if not df_csv.empty:
        status_filter = st.multiselect("Filter by Status", options=df_csv['Status'].unique(),
                                       default=df_csv['Status'].unique())

        filtered_df = df_csv[df_csv['Status'].isin(status_filter)]
        st.dataframe(filtered_df, width='stretch')

        st.download_button(
            label="Download Log as CSV",
            data=filtered_df.to_csv(index=False).encode('utf-8'),
            file_name='filtered_application_log.csv',
            mime='text/csv',
        )
    else:
        st.info("No CSV log data available to display.")

    # --- API USAGE SECTION ---
    st.markdown("---")
    st.subheader("🤖 API Usage Analytics")
    
    api_log_path = os.path.join(WORK_DIR, "api_usage_log.csv")
    if os.path.exists(api_log_path):
        try:
            api_df = pd.read_csv(api_log_path)
            
            # Ensure date column validity
            if 'Date' in api_df.columns and 'Purpose' in api_df.columns:
                # Group by Date and Purpose
                # We count the number of rows as "Calls"
                usage_summary = api_df.groupby(['Date', 'Purpose']).size().reset_index(name='Count')
                
                # Chart: Stacked Bar
                fig_usage = px.bar(
                    usage_summary, 
                    x='Date', 
                    y='Count', 
                    color='Purpose', 
                    title='Daily API Call Volume by Purpose',
                    labels={'Count': 'Number of Calls'},
                    text_auto=True
                )
                fig_usage.update_layout(barmode='stack')
                st.plotly_chart(fig_usage, width="stretch")
                
                # Stats Summary
                total_calls = len(api_df)
                today_str = datetime.now().strftime("%Y-%m-%d")
                today_calls = len(api_df[api_df['Date'] == today_str])
                
                u1, u2, u3 = st.columns(3)
                u1.metric("Total API Calls (All Time)", total_calls)
                u2.metric("Calls Today", today_calls)
                u3.metric("Unique Purposes", api_df['Purpose'].nunique())
                
            else:
                st.warning("API Log file format seems incorrect (Columns missing).")
        except Exception as e:
            st.error(f"Error loading API log: {e}")
    else:
        st.info("No API usage data recorded yet. Run the bot to generate stats.")

if __name__ == "__main__":
    main()