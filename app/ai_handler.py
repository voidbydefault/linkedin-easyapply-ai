import os
import sys
import json
import re
import time
import hashlib
import difflib
from datetime import datetime
from google import genai
from google.api_core import exceptions
import PyPDF2

# ML Imports
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    HAS_ML = True
except ImportError:
    HAS_ML = False
    print("Warning: scikit-learn not found. Local ML features disabled.")

try:
    from app.defaults import DEFAULT_SEEDS
except ImportError:
    from defaults import DEFAULT_SEEDS


class AIHandler:
    def __init__(self, config):
        self.config = config
        self.api_key = config['gemini_api_key']
        self.model_name = config['model_name']
        self.settings = config['ai_settings']
        self.work_dir = os.path.join(os.getcwd(), 'work')

        self.client = genai.Client(api_key=self.api_key)

        if not os.path.exists(self.work_dir):
            os.makedirs(self.work_dir)

        self.profile_path = os.path.join(self.work_dir, "user_profile.txt")
        self.positions_path = os.path.join(self.work_dir, "ai_positions.txt")
        self.cache_path = os.path.join(self.work_dir, "ai_cache.json")
        self.qa_cache_path = os.path.join(self.work_dir, "qa_cache.json")
        self.seeds_path = os.path.join(self.work_dir, "ml_seeds.json")
        
        self.cache = self.load_cache()
        self.qa_cache = self.load_qa_cache()
        
        self.api_log_path = os.path.join(self.work_dir, "api_usage_log.csv")
        self._ensure_api_log_exists()
        
        # --- Local Intelligence Setup ---
        self.tfidf_vectorizer = None
        self.tfidf_matrix = None
        self.knowledge_base_questions = []
        self.knowledge_base_answers = []
        self.init_local_intelligence()
        self.init_usage_tracker()

    def _ensure_api_log_exists(self):
        if not os.path.exists(self.api_log_path):
            try:
                with open(self.api_log_path, 'w', encoding='utf-8', newline='') as f:
                    f.write("Date,Timestamp,Purpose,Status\n")
            except Exception as e:
                print(f"Warning: Could not create API log file: {e}")

    def _log_api_call(self, purpose, status="Success"):
        try:
            now = datetime.now()
            date_str = now.strftime("%Y-%m-%d")
            time_str = now.strftime("%H:%M:%S")
            
            with open(self.api_log_path, 'a', encoding='utf-8', newline='') as f:
                f.write(f"{date_str},{time_str},{purpose},{status}\n")
        except Exception as e:
            print(f"Warning: Failed to log API call: {e}")

    def load_cache(self):
        if os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return {}
        return {}

    def save_cache(self):
        try:
            with open(self.cache_path, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, indent=2)
        except Exception as e:
            print(f"Warning: Could not save AI cache: {e}")
            
    def load_qa_cache(self):
        if os.path.exists(self.qa_cache_path):
            try:
                with open(self.qa_cache_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return {}
        return {}
        
    def save_qa_cache(self):
        try:
            with open(self.qa_cache_path, 'w', encoding='utf-8') as f:
                json.dump(self.qa_cache, f, indent=2)
        except Exception as e:
            print(f"Warning: Could not save QA cache: {e}")

    def get_cache_key(self, prompt_data):
        """Generates a unique hash for the prompt input."""
        if isinstance(prompt_data, dict):
            data_str = json.dumps(prompt_data, sort_keys=True)
        else:
            data_str = str(prompt_data)
        
        return hashlib.md5(data_str.encode('utf-8')).hexdigest()

    def get_seeds(self):
        """Loads seeds from disk or uses defaults."""
        if os.path.exists(self.seeds_path):
            try:
                with open(self.seeds_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                pass # Fallback
        
        # Default Seeds
        defaults = DEFAULT_SEEDS
        
        # Bootstrap file
        self.save_seeds(defaults)
        return defaults

    def save_seeds(self, seeds):
        try:
            with open(self.seeds_path, 'w', encoding='utf-8') as f:
                json.dump(seeds, f, indent=2)
        except Exception as e:
            print(f"Warning: Could not save ML seeds: {e}")

    def init_local_intelligence(self, force_reload=False):
        """Prepares the ML model for semantic matching."""
        if not HAS_ML: return
        
        
        # 1. Load Seeds (Persistent)
        seeds = self.get_seeds()
        
        self.knowledge_base_questions = [s[0] for s in seeds]
        self.knowledge_base_answers = [s[1] for s in seeds]
        
        # 2. Integrate History (QA Cache) - The "Learning" Part
        # QA Cache format: {"Question Text": "Answer Text"}
        for q, a in self.qa_cache.items():
            self.knowledge_base_questions.append(q)
            self.knowledge_base_answers.append(f"raw:{a}") # 'raw:' prefix means use literal answer
            
        # 3. Train Model
        if self.knowledge_base_questions:
            self.tfidf_vectorizer = TfidfVectorizer().fit(self.knowledge_base_questions)
            self.tfidf_matrix = self.tfidf_vectorizer.transform(self.knowledge_base_questions)


    def resolve_intent_value(self, answer_key):
        """Resolves abstract keys (e.g. 'chk:visa') to actual values from config."""
        if answer_key.startswith("raw:"):
            return answer_key[4:] # Return literal cached answer
            
        parts = answer_key.split(":")
        category = parts[0]
        key = parts[1]
        
        # Access config values
        
        if category == "chk":
            # Boolean Checkbox
            val = self.config.get('checkboxes', {}).get(key, None)
            
            if val is not None:
                # For visa_sponsorship, if config says False, answer is No.
                # If config says True, answer is Yes.
                if key == 'visa_sponsorship':
                    return "Yes" if val else "No"
                # For other checkboxes, assume True means Yes, False means No.
                return "Yes" if val else "No"
            
        elif category == "val":
            # Specific value like GPA
            if key == 'universityGpa':
                return str(self.config.get('universityGpa', '3.5')) # Default GPA if not found
            
        elif category == "exp":
            # Experience years
            if key == 'default':
                # Default to 0/Empty instead of hardcoded 3 or 2
                return str(self.config.get('experience', {}).get('default', 0))
            
        elif category == "pi":
            # Personal Information
            return self.config.get('personalInfo', {}).get(key, "")
            
        return None

    def init_usage_tracker(self):
        self.usage_file = os.path.join(self.work_dir, "ai_usage.json")
        
        # Determine RPD based on model (if not overridden)
        default_rpd = 20
        if "gemma" in self.model_name.lower():
            default_rpd = 14400
        
        # Safe Integer Parsing (User might put "14,400" in config)
        raw_rpd = self.settings.get('max_rpd', default_rpd)
        try:
            if isinstance(raw_rpd, str):
                raw_rpd = raw_rpd.replace(',', '').strip()
            self.max_rpd = int(raw_rpd)
        except ValueError:
            print(f"Warning: Invalid max_rpd '{raw_rpd}'. Using default {default_rpd}.")
            self.max_rpd = default_rpd

    def track_api_usage(self):
        """Tracks daily API calls and warns/blocks if limit is reached."""
        today = time.strftime("%Y-%m-%d")
        usage_data = {"date": today, "count": 0}
        
        if os.path.exists(self.usage_file):
            try:
                with open(self.usage_file, 'r') as f:
                    data = json.load(f)
                    if data.get("date") == today:
                        usage_data = data
            except:
                pass

        usage_data["count"] += 1
        current_count = usage_data["count"]
        
        # Save
        with open(self.usage_file, 'w') as f:
            json.dump(usage_data, f)
            
        print(f" [API Usage: {current_count}/{self.max_rpd}]")
        
        if current_count >= self.max_rpd:
            print(f"WARNING: API Quota ({self.max_rpd}) reached.")
            raise Exception("API_LIMIT_REACHED") 

    def get_usage_stats(self):
        """Returns (current_count, max_limit)"""
        if os.path.exists(self.usage_file):
            try:
                with open(self.usage_file, 'r') as f:
                    data = json.load(f)
                    return data.get("count", 0), self.max_rpd
            except:
                pass
        return 0, self.max_rpd

    def reset_usage(self):
        """Resets the daily usage counter."""
        today = time.strftime("%Y-%m-%d")
        usage_data = {"date": today, "count": 0}
        try:
            with open(self.usage_file, 'w') as f:
                json.dump(usage_data, f)
            print(" -> API Usage Counter Reset to 0.")
        except Exception as e:
            print(f"Failed to reset usage: {e}")

    def call_gemini(self, prompt, retries=3, purpose="General"):
        """Wrapper for API calls with caching and rate limit handling."""
        
        # 1. Check Cache
        cache_key = self.get_cache_key(prompt)
        if cache_key in self.cache:

            return self.cache[cache_key]

        # 2. Track Usage
        self.track_api_usage()

        # 3. Call API
        for attempt in range(retries):
            try:
                
                final_prompt = prompt
                if isinstance(prompt, dict):
                    final_prompt = json.dumps(prompt)

                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=final_prompt
                )
                result = response.text
                
                # 4. Save to Cache
                self.cache[cache_key] = result
                self.save_cache()
                
                # 5. Log Success
                self._log_api_call(purpose, status="Success")
                
                return result

            except exceptions.ResourceExhausted:
                wait_time = (attempt + 1) * 20 # 20s, 40s, 60s
                print(f" [429 ERROR] Rate Limit Exceeded. Waiting {wait_time}s before retry {attempt+1}/{retries}...")
                self._log_api_call(purpose, status="RateLimit") # Log retry
                time.sleep(wait_time)
                
                # Fatal Exit on last retry failure
                if attempt == retries - 1:
                    print("\nCritical: Persistent 429 Errors (Rate Limit). Terminating Bot safely. !!!")
                    self._log_api_call(purpose, status="Failed_RateLimit")
                    raise Exception("API_LIMIT_REACHED")
                    
            except Exception as e:
                print(f"AI Error (Attempt {attempt+1}): {e}")
                self._log_api_call(purpose, status=f"Error: {str(e)[:20]}")
                time.sleep(2)
        
        return None

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
        """
        Dynamically converts config.yaml dictionary into a readable text block for the AI.
        Iterates through ALL keys recursively, excluding sensitive fields.
        """
        text = "\n\n=== USER CONFIGURATION & PREFERENCES (SOURCE OF TRUTH) ===\n"
        
        # Keys to strictly exclude (Secrets)
        EXCLUDED_KEYS = [
            'email', 'password', 'gemini_api_key', 'linkedin_password', 
            'linkedin_email', 'api_key', 'access_token', 'client_secret'
        ]

        def recurse_format(data, indent=0):
            out = ""
            if isinstance(data, dict):
                for key, val in data.items():
                    if key in EXCLUDED_KEYS: continue
                    
                    if isinstance(val, (dict, list)):
                        out += f"{' ' * indent}- {key}:\n"
                        out += recurse_format(val, indent + 2)
                    else:
                        out += f"{' ' * indent}- {key}: {val}\n"
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, (dict, list)):
                        out += recurse_format(item, indent + 2)
                    else:
                        out += f"{' ' * indent}- {item}\n"
            return out

        text += recurse_format(config_params)
        text += "====================================================\n"
        return text

    def generate_user_profile(self, resume_path, config_params=None):
        """Generates the profile using Resume + Config Data."""
        if os.path.exists(self.profile_path):
            print(f" -> Loading existing profile from {self.profile_path}")
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

        response_text = self.call_gemini(prompt, purpose="Profile Generation")
        if response_text:
            with open(self.profile_path, 'w', encoding='utf-8') as f:
                f.write(response_text)
            return response_text
        else:
            print("Failed to generate profile after retries.")
            return "Profile generation failed."

    def generate_positions(self, profile_text):
        if os.path.exists(self.positions_path):
            print(f" -> Loading existing positions from {self.positions_path}")
            with open(self.positions_path, 'r', encoding='utf-8') as f:
                return [line.strip() for line in f.readlines() if line.strip()]

        print("Generating suggested job titles via AI...")
        prompt = (
            f"Based on this professional profile, list 10-15 relevant LinkedIn job search titles. "
            f"Return ONLY the titles, one per line, no bullet points.\n\nProfile:\n{profile_text}"
        )
        
        response_text = self.call_gemini(prompt, purpose="Position Generation")
        if response_text:
            positions = [p.strip() for p in response_text.split('\n') if p.strip()]
            with open(self.positions_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(positions))
            return positions
        else:
            return []

    def check_heuristics(self, job_text, user_profile):
        """
        Fast, local filter to reject jobs BEFORE calling AI.
        Returns: (Score, Reason) or None. 
        If None, proceed to AI.
        """
        text_lower = job_text.lower()
        
        # Security Clearance
        if "security clearance" in text_lower or "secret clearance" in text_lower or "top secret" in text_lower:
            if "clearance" not in user_profile.lower():
                return 0, "Heuristic: Security Clearance required but not in profile"

        # Citizenship (US Specific Common Pattern)
        if "us citizen" in text_lower and "citizen" not in user_profile.lower():
             if "us citizen only" in text_lower or "only us citizen" in text_lower:
                 pass 

        return None 

    def evaluate_single_job(self, job_text, user_profile):
        # Heuristic Check (Save API Calls)
        heuristic_result = self.check_heuristics(job_text, user_profile)
        if heuristic_result:
            return heuristic_result[0], heuristic_result[1]

        # --- PERSONA: OBJECTIVE SCREENER ---
        prompt_dict = {
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
        
        json_prompt = json.dumps(prompt_dict)
        raw_text = self.call_gemini(json_prompt, purpose="Job Screening")

        if not raw_text:
            return 0, "AI Call Failed"

        try:
            match = re.search(r'\{.*\}', raw_text, re.DOTALL)
            if match:
                json_str = match.group(0)
                data = json.loads(json_str)
                return int(data.get('score', 0)), data.get('reason', 'No reason provided')
            else:
                return 0, "AI JSON Parse Error"
        except Exception as e:
            return 0, "AI Error"

    def evaluate_batch(self, jobs_batch, user_profile):
        """
        Screens a batch of jobs in ONE API call.
        jobs_batch: List of {'id': str, 'text': str}
        Returns: Dict { job_id: {'score': int, 'reason': str} }
        """
        if not jobs_batch:
            return {}

        # Construct the bulk prompt
        jobs_text = ""
        for job in jobs_batch:
            # Truncate each job to avoid context overflow if batch is huge
            j_text = job['text'][:2000].replace('\n', ' ') 
            jobs_text += f"\n[JOB_ID: {job['id']}]\n{j_text}\n"

        prompt_dict = {
            "instruction": (
                "You are an expert technical recruiter. Screen these jobs against the Candidate Profile."
                "\n\nSCORING RULES:"
                "\n1. **Experience Match**: Primary factor."
                "\n2. **False Positives**: Strict check for different industries."
                "\n3. **Optimistic Logistics**: Ignore location/visa unless strictly forbidden."
                "\n\nOUTPUT: Return a JSON dictionary where keys are JOB_IDs and values are objects with 'score' and 'reason'."
            ),
            "candidate_profile": user_profile,
            "jobs_to_screen": jobs_text,
            "output_example": {
                "job_url_1": {"score": 85, "reason": "Good match"},
                "job_url_2": {"score": 10, "reason": "Wrong stack"}
            }
        }

       
        json_prompt = json.dumps(prompt_dict)
        raw_text = self.call_gemini(json_prompt, purpose="Job Screening (Batch)")

        results = {}
        if raw_text:
            try:
                match = re.search(r'\{.*\}', raw_text, re.DOTALL)
                if match:
                    json_str = match.group(0)
                    data = json.loads(json_str)
                    
                    # Normalize output
                    for j_id, res in data.items():
                        results[j_id] = {
                            'score': int(res.get('score', 0)),
                            'reason': res.get('reason', 'N/A')
                        }
            except Exception as e:
                print(f"Batch Analysis Error: {e}")
        
        # Fill missing with 0
        for job in jobs_batch:
            if job['id'] not in results:
                results[job['id']] = {'score': 0, 'reason': 'Batch Error / Parsing Failed'}
                
        # Cache the Individual Results (Important!)
        for j_id, res in results.items():
            pass

        return results

    def answer_question(self, question, options, user_profile):
        # Layer 1: Strict PII Injection (Rule-Based Override)
        # This prevents AI from hallucinating or being verbose on critical fields (Phone, Email, etc.)
        q_lower = question.lower()
        
        # Phone / Mobile
        if any(k in q_lower for k in ['phone', 'mobile', 'celular', 'móvil', 'number', 'contact']):
            # Return configured mobile number
            mob = self.config.get('personalInfo', {}).get('Mobile Phone Number', '')
            if mob:
                print(f"  [QA Strict Rule] '{question}' -> Injected Mobile Number")
                return mob
                
        # Email
        if 'email' in q_lower or 'correo' in q_lower:
            email = self.config.get('email', '')
            if email:
                print(f"  [QA Strict Rule] '{question}' -> Injected Email")
                return email
        
        # LinkedIn
        if 'linkedin' in q_lower:
             li = self.config.get('personalInfo', {}).get('Linkedin', '')
             if li:
                 print(f"  [QA Strict Rule] '{question}' -> Injected LinkedIn URL")
                 return li
             
        # --- Location Strict Rules ---
        p_info = self.config.get('personalInfo', {})
        
        # City
        if 'city' in q_lower:
            val = p_info.get('City', '')
            if val:
                print(f"  [QA Strict Rule] '{question}' -> Injected City ({val})")
                return val
                
        # State / Province
        if 'state' in q_lower or 'province' in q_lower or 'region' in q_lower:
            val = p_info.get('State', '')
            if val:
                print(f"  [QA Strict Rule] '{question}' -> Injected State ({val})")
                return val
                
        # Country
        if 'country' in q_lower:
            val = p_info.get('Country', '')
            if val:
                print(f"  [QA Strict Rule] '{question}' -> Injected Country ({val})")
                return val

        # Zip / Postal
        if 'zip' in q_lower or 'postal' in q_lower:
            val = p_info.get('Zip', '')
            if val:
                print(f"  [QA Strict Rule] '{question}' -> Injected Zip ({val})")
                return val
                
        # Street Address
        if 'address' in q_lower and 'email' not in q_lower:
            val = p_info.get('Street address', '')
            if val:
                 print(f"  [QA Strict Rule] '{question}' -> Injected Address")
                 return val

        # Layer 2: Exact Match (QA Cache)
        if question in self.qa_cache:

            raw_ans = self.qa_cache[question]
            clean_ans = self.clean_answer(raw_ans, question)
            
            # Self-Repair: If cleaning changed the answer, update the cache!
            if raw_ans != clean_ans:
                print(f"  [QA Cache Repair] '{raw_ans}' -> '{clean_ans}'")
                self.qa_cache[question] = clean_ans
                self.save_qa_cache()
                
            return clean_ans
            
        # Layer 3: Fuzzy Match (Difflib)
        # Check against existing keys in qa_cache
        closest_matches = difflib.get_close_matches(question, self.qa_cache.keys(), n=1, cutoff=0.95)
        if closest_matches:
            match = closest_matches[0]

            return self.qa_cache[match]
            
        # Layer 3: ML Intent (Scikit-Learn)
        if HAS_ML and self.tfidf_vectorizer and self.knowledge_base_questions:
            try:
                # Transform input question
                query_vec = self.tfidf_vectorizer.transform([question])
                # Calculate similarity
                similarities = cosine_similarity(query_vec, self.tfidf_matrix).flatten()
                best_idx = similarities.argmax()
                best_score = similarities[best_idx]
                
                if best_score > 0.5: # Threshold for similarity (Lowered for short text)
                    # We found a similar known question/intent!
                    target_key = self.knowledge_base_answers[best_idx]
                    resolved_answer = self.resolve_intent_value(target_key)
                    
                    if resolved_answer:
                        print(f"  [QA ML Intent] '{question}' -> Intent: {target_key} (Score: {best_score:.2f})")
                        return resolved_answer
                else:
                    print(f"  [QA ML Miss] Best Score: {best_score:.2f} (Threshold: 0.5)")
            except Exception as e:
                print(f"ML Error during question answering: {e}")

        custom_instructions = self.settings.get('custom_instruction_prompt', '').strip()
        custom_block = ""
        if custom_instructions:
            custom_block = f"\n*** GLOBAL USER INSTRUCTIONS (OVERRIDES DEFAULT BEHAVIOR) ***\n{custom_instructions}\n*********************************************************\n"

        prompt_dict = {
            "instruction": (
                f"Act as the candidate described in the profile. You are filling out a job application form."
                f"\n\nPROFILE (Source of Truth):\n{user_profile}\n\n"
                f"{custom_block}"
                f"QUESTION: {question}\n"
                f"OPTIONS/TYPE: {options}\n\n"
                f"=== INSTRUCTIONS ===\n"
                f"1. **Tone**: Professional, confident, and direct.\n"
                f"2. **Logic**: Infer the best positive answer from the profile. If 'years experience' is asked and you have 2015-2023, calculate 8. If asked 'Do you have X', answer Yes if you have it.\n"
                f"3. **Format**: Return a JSON object (NO Markdown).\n"
                f"   - 'answer': The value to put in the form.\n"
                f"   - 'type': One of ['numeric', 'text', 'boolean'] based on what the question is asking (regardless of language).\n"
            ),
            "output_schema": {
                "answer": "The clean value (e.g., '5', 'Yes', 'Software Engineer')",
                "type": "numeric | text | boolean"
            },
            "examples": [
                {"q": "¿Cuántos años de experiencia?", "out": {"answer": "5", "type": "numeric"}},
                {"q": "Mobile Phone", "out": {"answer": "+123456789", "type": "numeric"}},
                {"q": "Are you willing to relocate?", "out": {"answer": "Yes", "type": "boolean"}}
            ]
        }
        
        json_prompt = json.dumps(prompt_dict)
        response_text = self.call_gemini(json_prompt, purpose="Question Answering")
        
        if response_text:
            try:
                # 1. Parse JSON
                # Clean potential md blocks
                clean_text = response_text.replace('```json', '').replace('```', '')
                match = re.search(r'\{.*\}', clean_text, re.DOTALL)
                if match:
                    data = json.loads(match.group(0))
                    raw_ans = str(data.get('answer', ''))
                    q_type = data.get('type', 'text').lower()
                    
                    # 2. Universal Validation based on classification
                    final_ans = self.validate_universal_answer(raw_ans, q_type, question)
                    
                    # Save to QA Cache (Learning)
                    self.qa_cache[question] = final_ans
                    self.save_qa_cache()
                    return final_ans
                else:
                    # Fallback if no JSON
                    return response_text.strip()
            except Exception as e:
                print(f"JSON Parse Error in QA: {e}")
                return response_text.strip()
            
        return None

    def validate_universal_answer(self, answer, q_type, question):
        """Universal validation using AI-determined type."""
        clean = str(answer).strip()
        
        # 1. Numeric Validation (Universal)
        if q_type == 'numeric':
            # Handle "Yes/No" hallucinations for numeric fields
            if clean.lower() in ['yes', 'no', 'si', 'sí', 'no']:
                # Rescue logic: If question implies experience, default to 3
                if any(k in question.lower() for k in ['year', 'experi', 'año']):
                    default_exp = self.config.get('experience', {}).get('default', 3)
                    print(f"  [Validator] Rescued Boolean for Numeric (Exp) -> {default_exp}")
                    return str(default_exp)
                return "0" 

            # Extract digits/money
            # Allow digits, dots, commas, plus (phones) and spaces
            # Regex: Start with digit/plus, continue with digits/dots/commas/spaces
            match = re.search(r'[+\d][\d.,\s]*', clean)
            if match:
                 val = match.group(0).replace(' ', '')
                 # Naive comma cleanup (e.g. 50,000 -> 50000)
                 # Only if it looks like a separator (comma followed by 3 digits)
                 if re.search(r',\d{3}', val):
                     val = val.replace(',', '')
                 return val
            else:
                return "0" # Strict numeric fallback

        # 2. Boolean Validation
        if q_type == 'boolean':
             if any(token in clean.lower() for token in ['yes', 'si', 'sí', 'true']):
                 return "Yes"
             return "No"

        # 3. Text Validation (Strip quotes if present)
        if clean.startswith('"') and clean.endswith('"'):
            clean = clean[1:-1]
            
        return clean

    def clean_answer(self, answer, question):   
        # Heuristic type detection for legacy cleanup
        q_lower = question.lower()
        if any(k in q_lower for k in ['phone', 'year', 'salary', 'gpa', 'number', 'años', 'salario']):
            return self.validate_universal_answer(answer, 'numeric', question)
