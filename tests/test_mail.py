"""Offline checks for mail.py (the agent's email codes and links) and the assistant's `email` command.
Run: python test_mail.py

No Gmail, no network: fake messages in Gmail's own format, a fake Gmail session.
"""
import base64
import io
import json
import os
import sys
import tempfile
import time

from nuauto import config

tmp = tempfile.mkdtemp()
config.LOCAL_DIR = os.path.join(tmp, "local")  # never the real local/ (the Gmail login lives there)
config.ANSWERS_PATH = os.path.join(tmp, "answers.json")
config.OTHER_ANSWERS_PATH = os.path.join(tmp, "answers_other.json")

from nuauto import assist  # noqa: E402
from nuauto import mail  # noqa: E402

mail.TOKEN = os.path.join(config.LOCAL_DIR, "gmail_token.json")
NOW = time.time()
SITE = "acme.wd5.myworkdayjobs.com"


def b64(text):
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def gmail(id_, sender, subject, plain="", rich="", age=60):
    """A message as Gmail's API returns it (format=full)."""
    parts = ([{"mimeType": "text/plain", "body": {"data": b64(plain)}}] if plain else []) + \
            ([{"mimeType": "text/html", "body": {"data": b64(rich)}}] if rich else [])
    return {"id": id_, "internalDate": str(int((NOW - age) * 1000)),
            "payload": {"mimeType": "multipart/alternative", "parts": parts,
                        "headers": [{"name": "From", "value": sender}, {"name": "Subject", "value": subject}]}}


def msg(g):
    """What recent() makes of one: the fields pick() reads."""
    payload = g["payload"]
    plain, rich = mail.parts(payload)
    return {"id": g["id"], "time": int(g["internalDate"]) / 1000, "from": mail.header(payload, "from"),
            "subject": mail.header(payload, "subject"), "text": mail.text_of(plain, rich), "links": mail.links(plain, rich)}


WORKDAY = gmail("w1", "Acme Robotics <acme@myworkday.com>", "Verify your candidate account", rich=(
    '<p>Thanks for creating an account with Acme Robotics.</p><p><a href="https://acme.wd5.myworkdayjobs.com/en-US/'
    'External/activate/9f8e7d?redirect=%2Fjob&amp;x=1">Verify my account</a></p>'
    '<p><a href="https://acme.wd5.myworkdayjobs.com/privacy">Privacy</a> <a href="https://www.myworkday.com/unsubscribe">'
    'Unsubscribe</a> <a href="https://evil.example/verify">Click</a></p>'))
GREENHOUSE = gmail("g1", "Acme Robotics <no-reply@us.greenhouse-mail.io>", "Security code for your application to Acme Robotics", age=30,
                   plain="Copy and paste this code into the security code field on your application:\n\npT7zQ9wE\n\n"
                         "This code will expire in 10 minutes. Class of 2029 students welcome.")
ORACLE = gmail("o1", "Acme Careers <careers@acme.com>", "Your one-time passcode",
               plain="Hello,\nYour one-time passcode is 482913. It expires in 15 minutes.\nReference 2027.")
OTHER = gmail("x1", "Globex Corp <jobs@globex.com>", "Your verification code", plain="Your code is 777777.")
OLD = gmail("o0", "Acme Careers <careers@acme.com>", "Your one-time passcode", plain="Your code is 111111.", age=3600)

# reading a message: Gmail's base64 parts, the text (HTML without tags when there is no plain part), the links
plain, rich = mail.parts(WORKDAY["payload"])
assert plain == "" and "Verify my account" in rich
w = msg(WORKDAY)
assert "Thanks for creating an account" in w["text"] and "<a" not in w["text"]
assert "https://acme.wd5.myworkdayjobs.com/en-US/External/activate/9f8e7d?redirect=%2Fjob&x=1" in w["links"]

# the code: after "code"/"passcode", or alone on its line, never a year; none when two different codes are there
assert mail.find_code(msg(GREENHOUSE)["text"]) == "pT7zQ9wE"
assert mail.find_code(msg(ORACLE)["text"]) == "482913"
assert mail.find_code("Your verification code:\n\n  105 772\n") is None  # a code with a space in it: the user reads it
assert mail.find_code("Use code 123456 or code 654321") is None
assert mail.find_code("Thanks for applying to Acme in 2027! We will review your application 48213.") is None  # no code word
assert mail.find_code("Your PIN: 2027") is None  # a year is not a code
# the link: to this site only, the verify one, never unsubscribe / privacy / another site
assert mail.find_link(w["links"], SITE) == "https://acme.wd5.myworkdayjobs.com/en-US/External/activate/9f8e7d?redirect=%2Fjob&x=1"
assert mail.find_link(["https://evil.example/verify"], SITE) is None
assert mail.find_link(["https://acme.wd5.myworkdayjobs.com/a/verify/1", "https://acme.wd5.myworkdayjobs.com/b/confirm/2"], SITE) is None
assert mail.find_link(["https://acme.wd5.myworkdayjobs.com/one-link"], SITE) == "https://acme.wd5.myworkdayjobs.com/one-link"
# which messages are about this site: the company's name, or a link to the very host the browser is on
assert mail.company_names("Acme Robotics, Inc.") == ["acme robotics", "acme"]
assert mail.company_names("RTX") == ["rtx"] and mail.company_names("The Boeing Company") == ["the boeing"]
assert mail.about_site(msg(GREENHOUSE), "boards.greenhouse.io", "Acme Robotics")
assert not mail.about_site(msg(OTHER), "boards.greenhouse.io", "Acme Robotics")
assert mail.about_site(msg(WORKDAY), SITE, "Someone Else")  # it links to the host the browser is on

# pick: newest about this site first, within the last 15 minutes, not used before in this run
ms = [msg(g) for g in (OTHER, ORACLE, GREENHOUSE, OLD)]
got = mail.pick(ms, "boards.greenhouse.io", "Acme Robotics", now=NOW)
assert (got["status"], got["code"], got["from"], got["id"]) == ("code", "pT7zQ9wE", "Acme Robotics", "g1"), got  # the newest
assert mail.pick(ms, "boards.greenhouse.io", "Acme Robotics", used=("g1", "o1"), now=NOW) == {"status": "none"}  # the old one is out
assert mail.pick([msg(OTHER)], "boards.greenhouse.io", "Acme Robotics", now=NOW) == {"status": "none"}       # another company's
link = mail.pick([msg(WORKDAY)], SITE, "Acme Robotics", now=NOW)
assert link["status"] == "link" and link["link"].startswith("https://acme.wd5.myworkdayjobs.com/en-US/External/activate/")
assert mail.pick([msg(WORKDAY)], SITE, "Acme Robotics", want="code", now=NOW)["status"] == "ambiguous"  # the email, no code
two = gmail("t1", "Acme <a@acme.com>", "Your code", plain="Use code 123456 or code 654321")
assert mail.pick([msg(two)], "acme.com", "Acme", now=NOW)["status"] == "ambiguous"


# Gmail itself (a fake session): the search asks for the last 15 minutes only, reads each message, newest first
class Resp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class FakeGmail:
    def __init__(self, rounds):
        self.rounds, self.calls, self.queries = rounds, 0, []

    def get(self, url, params=None, timeout=None):
        if url.endswith("/messages"):
            self.queries.append(params["q"])
            batch = self.rounds[min(self.calls, len(self.rounds) - 1)]
            self.calls += 1
            self.batch = {g["id"]: g for g in batch}
            return Resp({"messages": [{"id": i} for i in self.batch]})
        return Resp(self.batch[url.rsplit("/", 1)[1]])


fake = FakeGmail([[], [OTHER, ORACLE]])  # the email arrives on the second look
slept = []
got = mail.lookup("acme.taleo.net", "Acme", "", "code", (), wait=30, s=fake, sleep=slept.append)
assert got["status"] == "code" and got["code"] == "482913" and slept == [mail.POLL], (got, slept)
assert "after:" in fake.queries[0] and "-in:trash" in fake.queries[0] and "-in:sent" in fake.queries[0]
assert int(fake.queries[0].split("after:")[1].split()[0]) >= int(time.time() - mail.WINDOW - 5)
assert mail.lookup("accounts.google.com", "Acme", s=fake)["status"] == "refused"  # never an identity provider's code
assert mail.lookup("neuidmsso.neu.edu", "Acme", s=fake)["status"] == "refused"
# no login: NoAccess (the agent asks the user)
assert not mail.ready()
try:
    mail.session()
    raise AssertionError("no login, yet a session")
except mail.NoAccess as e:
    assert "nuauto login gmail" in str(e)

# the assistant's `email` command: only the code goes to the agent (no message, no id); the log names the sender only
d = tempfile.mkdtemp()
with open(os.path.join(d, "state.json"), "w") as f:
    json.dump({"current_url": "https://acme.taleo.net/careersection/apply", "refs": {}, "issued": {}, "issued_values": [],
               "answer_cmd": ["/venv/python", "/proj/assist.py"], "company": "Acme", "job_url": "https://acme.taleo.net/job/1",
               "guesses": {}, "typed": {}}, f)
os.environ[assist.STATE_ENV] = d
seen = []
mail.lookup = lambda site, company, job, want, used: (seen.append((site, company, job, want, list(used))) or
                                                      {"status": "code", "code": "482913", "from": "Acme Careers",
                                                       "received": "13:20", "id": "o1"})


def run(*argv):
    out, sys.stdout = sys.stdout, io.StringIO()
    try:
        assist.bank(list(argv))
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout = out


r = run("email", "--want", "code")
assert r == {"status": "code", "code": "482913", "from": "Acme Careers", "received": "13:20",
             "next": "Type the code where the page asks for it."}, r
assert seen == [("acme.taleo.net", "Acme", "acme.taleo.net", "code", [])]
st = json.load(open(os.path.join(d, "state.json")))
assert st["email_used"] == ["o1"] and "482913" in st["issued_values"]  # typing it is not "typed with nothing behind it"
run("email")
assert seen[-1][4] == ["o1"]  # the same email is never handed out twice
logged = open(os.path.join(d, "actions.log")).read()
assert "email code -> code from Acme Careers (13:20)" in logged and "482913" not in logged
mail.lookup = lambda *a: (_ for _ in ()).throw(mail.NoAccess("Gmail access is not set up (nuauto login gmail)"))
r = run("email", "--want", "link")
assert r["status"] == "no_email_access" and r["next"] == "Ask the user to open the email."
assert assist.check_bash({"answer_cmd": ["/venv/python", "/proj/assist.py"]}, "/venv/python /proj/assist.py email --want code") is None
assert "email --want code|link" in assist.context_text("row 3", type("R", (), {"company": "A", "title": "I"})(), "https://x/1",
                                                       False, None, [], ["/venv/python", "/proj/assist.py"])
assert assist.secret_path(mail.TOKEN)  # the agent can never read the Gmail login (local/, and "token" in its name)

print("All email-code checks passed.")
