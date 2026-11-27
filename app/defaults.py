
import ipaddress
from urllib.parse import urlparse

DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"


def validate_local_llm_url(url):
    """Allow only http/https URLs pointing to loopback or RFC1918 private LAN addresses."""
    if not url or not isinstance(url, str):
        return False, "URL is empty"
    cleaned = url.strip()
    try:
        parsed = urlparse(cleaned)
    except Exception:
        return False, "Malformed URL"
    if parsed.scheme not in ("http", "https"):
        return False, f"Unsupported scheme '{parsed.scheme}'"
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False, "Missing hostname"
    if host == "localhost" or host.endswith(".localhost"):
        return True, None
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False, f"Non-loopback/non-private hostname '{host}' is blocked"
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        return False, f"Unsafe address '{host}' is blocked"
    if ip.is_loopback or ip.is_private:
        return True, None
    return False, f"Public or non-private IP '{host}' is blocked"


def get_default_gemini_rpd(model_name):
    """Returns the default free-tier daily request limit (RPD) for a Gemini/Gemma model."""
    name = (model_name or DEFAULT_GEMINI_MODEL).lower()
    if "gemma" in name:
        return 14400
    if "flash-lite" in name:
        return 500
    if "flash" in name:
        return 250
    return 100

DEFAULT_SEEDS = [
    ["Will you now or in the future require sponsorship for employment visa status?", "chk:visa_sponsorship"],
    ["Do you need a visa to work?", "chk:visa_sponsorship"],
    ["Do you have a valid work permit?", "chk:legallyAuthorized"], 
    ["Do you have a transferable iqama?", "chk:legallyAuthorized"],
    ["Do you have a valid residency permit?", "chk:legallyAuthorized"],
    
    ["Have you completed the following level of education: Bachelor's Degree?", "chk:degreeCompleted"],
    ["Do you have a Bachelor's degree?", "chk:degreeCompleted"],
    ["What is your cumulative GPA?", "val:universityGpa"],
    
    ["How many years of work experience do you have?", "exp:default"],
    ["Total years of experience?", "exp:default"],
    ["Years of professional experience?", "exp:default"],
    
    ["What is your mobile phone number?", "pi:Mobile Phone Number"],
    ["First Name", "pi:First Name"],
    ["Last Name", "pi:Last Name"],
    ["Email Address", "pi:Email Address"],
    
    ["Are you comfortable commuting to this job's location?", "chk:commute"],
    ["Can you relocate?", "chk:relocation"],
    ["Are you willing to relocate for this position?", "chk:relocation"],
    ["Are you open to hybrid work?", "chk:hybrid"],
    ["Are you open to remote work?", "chk:remote"],
    ["Are you open to on-site work?", "chk:on_site"],
    
    ["What is your gender?", "raw:I prefer not to specify"],
    ["Gender", "raw:I prefer not to specify"],
    ["What is your race?", "raw:I prefer not to specify"],
    ["Race", "raw:I prefer not to specify"],
    ["Are you a veteran?", "raw:I prefer not to specify"],
    ["Veteran status", "raw:I prefer not to specify"],
    ["Do you have a disability?", "raw:I prefer not to specify"],
    ["Disability status", "raw:I prefer not to specify"]
]
