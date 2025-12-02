import os
import json
import re
import google.generativeai as genai
import PyPDF2


class AIHandler:
    def __init__(self, config):
        self.config = config
        self.api_key = config['gemini_api_key']
        self.model_name = config['model_name']
        self.settings = config['ai_settings']
        self.work_dir = self.settings.get('work_dir', './work')

        genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel(self.model_name)

        if not os.path.exists(self.work_dir):
            os.makedirs(self.work_dir)

        self.profile_path = os.path.join(self.work_dir, "user_profile.txt")
        self.positions_path = os.path.join(self.work_dir, "ai_positions.txt")

    def prompt_user(self, prompt, valid_keys, default):
        valid_str = "/".join(valid_keys)
        while True:
            user_input = input(f"{prompt} [{valid_str}] (Default: {default}): ").strip().lower()
            if not user_input:
                return default
            if user_input in valid_keys:
                return user_input
            print(f"Invalid input. Please enter one of: {valid_keys}")

    def parse_resume(self, resume_path):
        text = ""
        try:
            with open(resume_path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    text += page.extract_text() + "\n"
        except Exception as e:
            print(f"Failed to parse resume: {e}")
        return text

    def format_config_to_text(self, config_params):
        """Converts config.yaml dictionary into a readable text block for the AI."""
        text = "\n\n=== USER PREFERENCES & HARD REQUIREMENTS (STRICT) ===\n"

        # Checkboxes
        if 'checkboxes' in config_params:
            text += "HARD REQUIREMENTS (Yes/No):\n"
            for key, val in config_params['checkboxes'].items():
                status = "YES/TRUE" if val is True else "NO/FALSE"
                if isinstance(val, list): status = f"One of: {', '.join(val)}"
                text += f"- {key}: {status}\n"

        # Personal Info
        if 'personalInfo' in config_params:
            text += "\nPERSONAL INFORMATION:\n"
            for key, val in config_params['personalInfo'].items():
                text += f"- {key}: {val}\n"

        # Experience
        if 'experience' in config_params:
            text += "\nYEARS OF EXPERIENCE:\n"
            for key, val in config_params['experience'].items():
                text += f"- {key}: {val} years\n"

        # Education/GPA
        if 'universityGpa' in config_params:
            text += f"\nGPA: {config_params['universityGpa']}\n"

        text += "====================================================\n"
        return text

    def generate_user_profile(self, resume_path, config_params=None):
        """Generates the profile using Resume + Config Data."""
        if os.path.exists(self.profile_path):
            if self.prompt_user("\n[1/3] user_profile.txt exists. Regenerate with new Config?", ['y', 'n'], "n") != 'y':
                with open(self.profile_path, 'r', encoding='utf-8') as f:
                    return f.read()

        print("Generating user profile from Resume + Config via AI...")
        resume_text = self.parse_resume(resume_path)

        config_text = ""
        if config_params:
            config_text = self.format_config_to_text(config_params)

        prompt = (
            f"You are creating a 'Source of Truth' profile for a job applicant. "
            f"Combine the Resume Text and the User Preferences below.\n"
            f"CRITICAL: The User Preferences override any assumptions from the resume regarding 'Hard Requirements' (Visa, etc).\n\n"

            f"--- LOGIC FOR CALCULATING EXPERIENCE ---\n"
            f"1. **Total Professional Experience:** You MUST calculate this by mathematically summing the duration of the roles listed in the Resume's 'Work Experience' section (e.g., 2015-2023 = 8 years). \n"
            f"   - DO NOT use the 'default' value from User Preferences for Total Experience.\n"
            f"   - If the resume lists dates, calculate the exact time (approx 9 years).\n"
            f"2. **Skill-Specific Experience:** If the resume does not explicitly state years for a specific skill, ONLY THEN check the User Preferences 'experience' section.\n"
            f"3. **Fallback:** Use the User Preferences 'default' value ONLY for specific skills that are missing from the resume, not for the candidate's seniority.\n"
            f"----------------------------------------\n\n"

            f"{config_text}\n\n"
            f"Resume Text:\n{resume_text}\n\n"
            f"Output a detailed professional profile including:\n"
            f"1. Professional Summary (Written in 3rd person. Clearly state Total Years of Experience calculated from Resume dates).\n"
            f"2. Hard Requirements Status (Visa, Driver's License, Hybrid, etc.)\n"
            f"3. Education & GPA\n"
            f"4. Experience & Skills (List calculated total years first, then specific skills)"
        )

        try:
            response = self.model.generate_content(prompt)
            profile_text = response.text
            with open(self.profile_path, 'w', encoding='utf-8') as f:
                f.write(profile_text)
            return profile_text
        except Exception as e:
            print(f"AI Error: {e}")
            return "Profile generation failed."

    def generate_positions(self, profile_text):
        if os.path.exists(self.positions_path):
            if self.prompt_user("\n[2/3] ai_positions.txt exists. Regenerate?", ['y', 'n'], "n") != 'y':
                with open(self.positions_path, 'r', encoding='utf-8') as f:
                    return [line.strip() for line in f.readlines() if line.strip()]

        print("Generating suggested job titles via AI...")
        prompt = (
            f"Based on this professional profile, list 10-15 relevant LinkedIn job search titles. "
            f"Return ONLY the titles, one per line, no bullet points.\n\nProfile:\n{profile_text}"
        )
        try:
            response = self.model.generate_content(prompt)
            positions = [p.strip() for p in response.text.split('\n') if p.strip()]
            with open(self.positions_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(positions))
            return positions
        except Exception as e:
            print(f"AI Error: {e}")
            return []

    def evaluate_single_job(self, job_text, user_profile):
        # --- PERSONA: OBJECTIVE SCREENER ---
        prompt = {
            "instruction": (
                "You are an expert technical recruiter. Your goal is to SCREEN this candidate for the job."
                "\n\nSCORING RULES:"
                "\n1. **Experience Match (Primary)**: Does the candidate have the core skills required? If the job is 'Senior' and candidate is 'Junior', score low, but give some advantage if experience gap is up to 3 years."
                "\n2. **False Positives**: Be strict. If the job description is for a completely different industry or role (e.g., Nurse vs Software Engineer), score 0."
                "\n3. **Optimistic Logistics**: Do NOT disqualify based on Location or Visa unless explicitly forbidden in the text (e.g., 'US Citizens Only'). Assume the candidate will relocate."
                "\n4. **Output**: Return valid JSON with a score (0-100) and a brief reason."
            ),
            "candidate_profile": user_profile,
            "job_description": job_text[:5000],
            "output_format": {
                "score": "0-100 (integer)",
                "reason": "Max 15 words explanation"
            }
        }

        try:
            response = self.model.generate_content(json.dumps(prompt))
            raw_text = response.text

            match = re.search(r'\{.*\}', raw_text, re.DOTALL)
            if match:
                json_str = match.group(0)
                data = json.loads(json_str)
                return int(data.get('score', 0)), data.get('reason', 'No reason provided')
            else:
                return 0, "AI JSON Parse Error"

        except Exception as e:
            return 0, "AI Error"

    def answer_question(self, question, options, user_profile):
        # --- PERSONA: THE CANDIDATE (YOU) ---
        prompt = (
            f"Act as the candidate described in the profile below. You are filling out a job application form."
            f"\n\nPROFILE (Source of Truth):\n{user_profile}\n\n"
            f"QUESTION: {question}\n"
            f"OPTIONS/TYPE: {options}\n\n"
            f"=== INSTRUCTIONS ===\n"
            f"1. **Tone**: First Person ('I', 'me', 'my'). Professional, confident, and direct.\n"
            f"2. **Strategy**: If the exact answer is missing from the profile, INFER the most logical positive answer based on your skills. Do NOT say 'The profile does not mention'.\n"
            f"3. **Constraints**: If the profile has a hard requirement (e.g., Visa: Yes), adhere to it strictly.\n"
            f"4. **Format**: Output ONLY the answer text. No conversational filler."
        )
        try:
            response = self.model.generate_content(prompt)
            return response.text.strip()
        except:
            return None