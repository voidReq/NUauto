"""What you study: a starting set of preferences per field (the setup wizard's "What do you study?").

Picking a field fills in the preferences that depend on it: the kinds of work jobs are sorted into (Claude's
category step, prompts/CATEGORY_PROMPT.md), who you are and which roles to keep / drop (Claude's triage,
prompts/TRIAGE_PROMPT.md), the major words that flag a posting for other majors, and the ranking bonuses. Everything
stays editable in the wizard afterwards. A config without "field" is engineering: this project's original settings
(jobs.DEFAULTS), so an existing setup does not change.

Each kind of work: key (what categories.json stores), label (what screens show), description (what Claude reads).
Every field has "other".
"""

ENGINEERING_CATEGORIES = [
    {"key": "security", "label": "Security",
     "description": "cybersecurity is a core part of the job: pentesting, offensive/red\n"
                    "  team, appsec/product security, security engineering, SOC/detection/incident\n"
                    "  response, vulnerability research, network/cloud security, IAM, security\n"
                    "  operations, security-focused GRC with technical work. If security is central,\n"
                    "  choose `security` even when the role is also embedded/hardware/software."},
    {"key": "embedded", "label": "Embedded",
     "description": "firmware, microcontrollers, RTOS, device drivers, embedded Linux."},
    {"key": "hardware", "label": "Hardware",
     "description": "circuits, PCB, electrical/power, RF/analog, chip/FPGA/ASIC/SoC design\n"
                    "  or verification, electrical engineering."},
    {"key": "systems", "label": "Systems",
     "description": "OS/kernel, networking stacks, compilers, performance, distributed\n"
                    "  systems infrastructure, low-level C/C++ systems software."},
    {"key": "robotics_test", "label": "Robotics / test",
     "description": "robotics, controls, autonomy, hardware test/validation, test\n"
                    "  automation of physical systems, lab/manufacturing test engineering."},
    {"key": "fullstack_web", "label": "Web / full-stack",
     "description": "the job is mainly web/app development: full-stack, front-end,\n"
                    "  web back-end/APIs for web products, UI, mobile apps."},
    {"key": "software", "label": "Software",
     "description": "other software engineering (tools, internal software, backend that is\n"
                    "  not mainly web, general SWE) that fits none of the above."},
    {"key": "data_ml", "label": "Data / ML",
     "description": "data science/analytics, ML/AI modeling, data engineering."},
    {"key": "it", "label": "IT",
     "description": "IT support, IT operations, sysadmin, help desk, AV/IT."},
    {"key": "other", "label": "Other",
     "description": "anything else (product, operations, manufacturing, research outside\n  the above)."},
]

def kinds(*items):
    """[(key, label, description)] -> the categories list; "other" is added at the end."""
    out = [{"key": k, "label": label, "description": d} for k, label, d in items]
    return out + [{"key": "other", "label": "Other", "description": "anything else."}]


FIELDS = {
    "engineering": {
        "label": "Engineering or computer science",
        "student": "2nd-year Electrical & Computer Engineering (math minor). Interests sit at\n"
                   "intersections of embedded systems, hardware, software, security/pentesting,\n"
                   "networking/Linux, robotics/controls, AR/VR, computer architecture.",
        "keep_roles": "software, firmware/embedded, electrical/computer/hardware engineering,\n"
                      "  test/validation, security/IT/networking, robotics/controls, data/ML, R&D,\n"
                      "  technical product or technical operations, lab/automation engineering, etc.",
        "drop_roles": "accounting, finance,\n"
                      "  marketing, sales, HR, nursing/clinical, pharmacy, law, purely biology/chemistry lab\n"
                      "  work, pure mechanical/civil design with no electrical/software side, teaching,\n"
                      "  hospitality.",
        "major_words": ["college of engineering", "electrical"],
        "categories": ENGINEERING_CATEGORIES,
        "category_bonus": {"security": 20, "embedded": 15, "hardware": 10, "systems": 10, "robotics_test": 10},
        "category_threshold": {"security": 60},
        "rank_last": ["fullstack_web"],
    },
    "business": {
        "label": "Business",
        "student": "Business student (D'Amore-McKim). Interests: finance, marketing, consulting, operations.",
        "keep_roles": "finance, accounting, marketing, consulting/strategy, operations/supply chain, sales/business "
                      "development, HR/people, business or data analytics, product or project management, "
                      "startups/entrepreneurship, etc.",
        "drop_roles": "software or hardware engineering, nursing/clinical care, pharmacy, lab science research, "
                      "teaching, skilled trades.",
        "major_words": ["business", "d'amore-mckim", "accounting", "finance", "marketing", "management",
                        "supply chain", "entrepreneurship"],
        "categories": kinds(
            ("finance", "Finance",
             "corporate finance, investment, banking, financial analysis, FP&A, wealth management."),
            ("accounting", "Accounting", "audit, tax, bookkeeping, controllership, accounting operations."),
            ("marketing", "Marketing",
             "brand, digital or product marketing, market research, content and social media for a business."),
            ("consulting", "Consulting / strategy",
             "management, strategy or operations consulting; strategy and business planning."),
            ("operations", "Operations / supply chain",
             "operations, logistics, procurement, supply chain, purchasing, business operations."),
            ("sales", "Sales / business development",
             "sales, account management, business development, partnerships, customer success."),
            ("hr", "HR / people", "human resources, recruiting, talent, people operations."),
            ("analytics", "Business analytics",
             "business or data analysis, business intelligence, reporting, dashboards."),
            ("product", "Product / project management", "product management, project or program management, PMO."),
            ("startup", "Startups / entrepreneurship",
             "generalist roles at early-stage companies, venture capital, incubators."),
        ),
        "category_bonus": {}, "category_threshold": {}, "rank_last": [],
    },
    "health": {
        "label": "Health sciences or nursing",
        "student": "Health sciences student (Bouvé College of Health Sciences). Interests: patient care, public "
                   "health, clinical research.",
        "keep_roles": "nursing/clinical care, pharmacy, physical/occupational/speech therapy and rehab, public or "
                      "community health, health administration, clinical research, health data or informatics, lab "
                      "work in health settings, etc.",
        "drop_roles": "software or hardware engineering, finance/accounting, marketing/sales, law, architecture/design.",
        "major_words": ["nursing", "health", "bouvé", "bouve", "pharmacy", "pharmaceutical", "physical therapy",
                        "public health", "physician assistant", "speech", "exercise"],
        "categories": kinds(
            ("nursing", "Nursing / clinical",
             "nursing, patient care, clinical support in hospitals, clinics or care homes."),
            ("pharmacy", "Pharmacy", "pharmacy practice, pharmacy technician, pharmaceutical services."),
            ("therapy", "Therapy / rehab",
             "physical, occupational or speech therapy, rehabilitation, athletic training, exercise science."),
            ("public_health", "Public / community health",
             "public health programs, community health, health education, epidemiology."),
            ("health_admin", "Health administration",
             "healthcare administration, operations or management, patient services, health policy work."),
            ("clinical_research", "Clinical research",
             "clinical trials, research coordination, research assistant work in health."),
            ("health_data", "Health data / informatics",
             "health informatics, health data analysis, electronic health records."),
        ),
        "category_bonus": {}, "category_threshold": {}, "rank_last": [],
    },
    "sciences": {
        "label": "Natural sciences or math",
        "student": "Science student (College of Science). Interests: lab research, data analysis.",
        "keep_roles": "lab research (biology, chemistry, biochemistry, neuroscience), environmental/marine/earth "
                      "science, physics/math/quantitative analysis, data analysis, biotech or pharma industry roles, "
                      "quality control/assurance, science communication or education, etc.",
        "drop_roles": "software engineering with no science side, sales, marketing, HR, accounting, hospitality, "
                      "nursing/clinical care.",
        "major_words": ["college of science", "biology", "chemistry", "biochemistry", "physics", "mathematics",
                        "neuroscience", "environmental", "marine", "behavioral neuroscience"],
        "categories": kinds(
            ("lab_research", "Lab research",
             "wet-lab or bench research in biology, chemistry, biochemistry, neuroscience."),
            ("environmental", "Environmental / earth / marine",
             "environmental, earth, marine or ecology work, field work, sustainability science."),
            ("quantitative", "Physics / math / quantitative",
             "physics, mathematics, statistics or quantitative modeling."),
            ("data", "Data analysis", "data analysis, data science, bioinformatics, computational work."),
            ("industry", "Biotech / pharma industry",
             "biotech or pharmaceutical industry roles: QA/QC, manufacturing, process development, regulatory."),
            ("science_comm", "Science communication / education",
             "science writing, outreach, museums, teaching science."),
        ),
        "category_bonus": {}, "category_threshold": {}, "rank_last": [],
    },
    "social": {
        "label": "Social sciences or humanities",
        "student": "Social sciences / humanities student (CSSH). Interests: policy, research, communications.",
        "keep_roles": "policy/government, legal or law offices, nonprofit/community work, "
                      "communications/PR/journalism, education/teaching, research or analysis (economics, polling, "
                      "think tanks), international affairs, human services, etc.",
        "drop_roles": "software or hardware engineering, nursing/clinical care, lab science research, accounting.",
        "major_words": ["social sciences", "humanities", "political", "economics", "psychology", "history", "english",
                        "philosophy", "sociology", "criminology", "international affairs", "anthropology"],
        "categories": kinds(
            ("policy", "Policy / government", "public policy, government offices, campaigns, advocacy."),
            ("legal", "Legal", "law firms, legal assistant or paralegal work, courts, compliance."),
            ("nonprofit", "Nonprofit / community", "nonprofits, community organizations, human services, social work."),
            ("communications", "Communications / journalism",
             "communications, PR, journalism, writing, editing, publishing."),
            ("education", "Education", "teaching, tutoring, education programs, school administration."),
            ("research", "Research / analysis",
             "social science research, economic or policy analysis, polling, think tanks."),
        ),
        "category_bonus": {}, "category_threshold": {}, "rank_last": [],
    },
    "arts": {
        "label": "Arts, media or design",
        "student": "Arts, media and design student (CAMD). Interests: design, media production, communication.",
        "keep_roles": "UX/UI and graphic design, film/video/media production, writing/journalism/editorial, "
                      "architecture, game design, music or arts administration, social media/marketing/content, "
                      "communications, etc.",
        "drop_roles": "software engineering with no design side, hardware engineering, nursing/clinical care, "
                      "accounting/finance, lab science research.",
        "major_words": ["arts, media and design", "camd", "design", "media", "architecture", "journalism",
                        "communication", "music", "game", "art", "theatre"],
        "categories": kinds(
            ("design", "UX / graphic design", "UX/UI, graphic, visual, product or interaction design."),
            ("media", "Film / video / media", "film, video, photography, audio, media production and post-production."),
            ("writing", "Writing / journalism", "journalism, editorial, copywriting, publishing."),
            ("architecture", "Architecture", "architecture, urban design, interior design."),
            ("games", "Game design", "game design, game art, interactive media."),
            ("arts_admin", "Arts / music administration",
             "museums, galleries, theatre, music industry, arts organizations."),
            ("marketing", "Social media / marketing", "social media, content, brand and marketing communications."),
        ),
        "category_bonus": {}, "category_threshold": {}, "rank_last": [],
    },
}
PRESET_KEYS = ("student", "keep_roles", "drop_roles", "major_words", "categories", "category_bonus",
               "category_threshold", "rank_last")  # what picking a field fills in


def preset(field):
    """The preferences a field fills in (copies: safe to change)."""
    import copy
    f = FIELDS[field]
    return {k: copy.deepcopy(f[k]) for k in PRESET_KEYS}


def fingerprint(categories):
    """Which set of kinds of work a job was sorted into (the keys, in any order): a job sorted under another set is
    sorted again (jobs.cmd_cat_export)."""
    return ",".join(sorted(c["key"] for c in categories))


ENGINEERING = fingerprint(ENGINEERING_CATEGORIES)  # what jobs sorted before fields existed were sorted into


def render_categories(categories):
    """The list in prompts/CATEGORY_PROMPT.md ({{categories}})."""
    return "\n".join(f"- `{c['key']}`: {c['description']}" for c in categories)
