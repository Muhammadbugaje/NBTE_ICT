import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import re
import lib
from lib import *
lib.SHOTS = os.path.join(S, "phase3"); os.makedirs(lib.SHOTS, exist_ok=True)
c = ctx(); p = page_for(c)
login_portal(p, "admin@demo-poly.local")
p.goto(POR + "/qa-requests/2/ssq/2/"); settle(p); p.wait_for_timeout(500)
lib.shot(p, "27_ssq_workspace_governance_prefill")
p.locator(".qa-ssq__nav-item a:has-text('Governance')").click(); settle(p); p.wait_for_timeout(700)
lib.shot(p, "28_ssq_workspace_governance_section")
p.fill("[name=f__council_chair]", "Alh. Musa Danladi") if p.locator("[name=f__council_chair]").count() else None
p.wait_for_timeout(2000); settle(p)
lib.shot(p, "29_ssq_workspace_autosaved")
c.close()
c = ctx(mobile=True); p = page_for(c)
login_portal(p, "admin@demo-poly.local")
p.goto(POR + "/qa-requests/2/ssq/2/"); settle(p)
lib.shot(p, "30_mobile_ssq_workspace")
c.close()
c = ctx(); p = page_for(c)
login_portal(p, "academic@demo-poly.local")
lib.shot(p, "31_department_user_dashboard")
c.close()
print("ERRORS", errors)
