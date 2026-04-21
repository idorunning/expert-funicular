"""Holding privacy / EULA / DPA text shown inside the Legal dialog.

The wording below is a DRAFT placeholder written by the product team. Before
shipping to a paying customer it MUST be reviewed and signed off by a UK
solicitor. Keeping the text in this isolated module makes that review cycle
safe — it can be edited without touching any widget or service code.
"""
from __future__ import annotations


PRODUCT_NAME = "Mortgage Broker Affordability Assistant"
PRODUCT_TAGLINE = "AI powered · Fully local · Fully secure"
COMPANY_NAME = "Mortgage Oasis Ltd"
COMPANY_WEBSITE = "https://www.mortgage-oasis.com"
COMPANY_EMAIL_PRIVACY = "privacy@mortgage-oasis.com"
COMPANY_EMAIL_SUPPORT = "support@mortgage-oasis.com"


PRIVACY_POLICY = f"""\
<h2>Privacy Policy (DRAFT — pending solicitor review)</h2>

<p><b>Publisher:</b> {COMPANY_NAME} ({COMPANY_WEBSITE}).</p>
<p><b>Effective date:</b> on first installation of this copy of
{PRODUCT_NAME}.</p>

<h3>1. What this application does</h3>
<p>{PRODUCT_NAME} is a desktop application that analyses bank statements
for mortgage affordability assessments. All processing runs locally on
your machine. Bank statement files, extracted transactions, and any
categorisation decisions are stored only on the device where the
application is installed.</p>

<h3>2. What data is collected</h3>
<p>The application stores the following information on your device:</p>
<ul>
  <li>User accounts (username, full name, email, hashed password).</li>
  <li>Client records (display name, reference, per-client folder).</li>
  <li>Imported bank-statement files and their parsed transactions.</li>
  <li>Transaction categories, broker corrections and merchant rules.</li>
  <li>An audit log of actions performed inside the application.</li>
  <li>Optional SMTP credentials if you opt in to email-based password reset.</li>
</ul>
<p>No data is transmitted to {COMPANY_NAME} or to any third party.</p>

<h3>3. Network traffic</h3>
<p>The application contacts only <code>127.0.0.1</code> (the local Ollama
server) for categorisation. It does not call any outbound URL. The only
exception is the optional SMTP path for password reset, which is disabled
by default and only activates if an administrator explicitly configures it.</p>

<h3>4. Responsibilities under UK GDPR / Data Protection Act 2018</h3>
<p>The broker (the user of this application) is the data controller for
any personal data they choose to import. {COMPANY_NAME} acts only as a
software supplier and does not have access to any client data. Brokers
are responsible for keeping their device, operating-system account, and
database password secure in line with UK GDPR Art. 5(1)(f) (integrity and
confidentiality).</p>

<h3>5. Retention and deletion</h3>
<p>Data is retained for as long as you keep it on the device. Deleting a
client from the application removes its statements and transactions via
cascade. Uninstalling the application does not automatically remove the
data folder under <code>%APPDATA%\\BrokerLedger</code> — delete it
manually if required.</p>

<h3>6. Subject access and complaints</h3>
<p>Because {COMPANY_NAME} holds no personal data, subject-access requests
should be made directly to the broker who imported the data. For product
questions contact
<a href="mailto:{COMPANY_EMAIL_PRIVACY}">{COMPANY_EMAIL_PRIVACY}</a>.</p>

<p><i>This is a draft, provided for information only, and does not
constitute legal advice.</i></p>
"""


EULA = f"""\
<h2>End-User Licence Agreement (DRAFT — pending solicitor review)</h2>

<p>This End-User Licence Agreement ("Agreement") is between you and
{COMPANY_NAME} ("Licensor"). By installing or using {PRODUCT_NAME} (the
"Software") you agree to the terms below.</p>

<h3>1. Licence grant</h3>
<p>The Licensor grants you a non-exclusive, non-transferable licence to
install and use the Software on devices you control for the purpose of
producing mortgage-affordability assessments in your own business.</p>

<h3>2. Restrictions</h3>
<p>You must not: (a) reverse-engineer, decompile or disassemble the
Software; (b) rent, lease, sublicense or redistribute copies; (c) remove
or alter any notices; (d) use the Software to provide a service bureau to
third parties without a separate written agreement.</p>

<h3>3. Ownership</h3>
<p>The Software and all intellectual-property rights in it remain the
property of the Licensor. No rights are granted other than those
expressly stated in this Agreement.</p>

<h3>4. No warranty</h3>
<p>The Software is provided "as is". The Licensor makes no warranty as to
fitness for a particular purpose, accuracy of categorisation, or
non-infringement. You are responsible for verifying that the Software's
output is fit for regulatory use in your jurisdiction.</p>

<h3>5. Limitation of liability</h3>
<p>To the fullest extent permitted by English law, the Licensor's total
liability under this Agreement is capped at the licence fee paid in the
twelve months preceding the event giving rise to the claim. The Licensor
is not liable for loss of profit, loss of data, or any indirect or
consequential loss.</p>

<h3>6. Governing law and jurisdiction</h3>
<p>This Agreement is governed by the laws of England and Wales. The
courts of England and Wales have exclusive jurisdiction over any dispute
arising under it.</p>

<h3>7. Termination</h3>
<p>This Agreement terminates automatically if you breach it. On
termination you must uninstall the Software and destroy any copies.</p>

<p>Contact for licensing questions:
<a href="mailto:{COMPANY_EMAIL_SUPPORT}">{COMPANY_EMAIL_SUPPORT}</a>.</p>

<p><i>This is a draft, provided for information only, and does not
constitute legal advice.</i></p>
"""


DPA = f"""\
<h2>Data Processing Agreement (DRAFT — pending solicitor review)</h2>

<p>This Data Processing Agreement ("DPA") supplements the End-User
Licence Agreement between you (the "Broker" / data controller) and
{COMPANY_NAME} (the "Supplier" / software publisher). It sets out the
parties' obligations under UK GDPR and the Data Protection Act 2018
(Schedule 2).</p>

<h3>1. Roles</h3>
<p>The Broker is the <b>data controller</b> for any personal data
processed using the Software. {COMPANY_NAME} is the <b>software
supplier</b>. Because the Software runs entirely on the Broker's device
and the Supplier has no access to client data, the Supplier does not
act as a data processor.</p>

<h3>2. Sub-processors</h3>
<p>The Supplier does not engage any sub-processor in respect of the
Broker's data. No cloud service, analytics provider, or external API is
used for categorisation or storage.</p>

<h3>3. Security of processing</h3>
<p>The Broker is responsible for implementing appropriate technical and
organisational measures (full-disk encryption, strong OS account
passwords, locked screens, physical security) for the device running
the Software. Optional database-level encryption is available via the
first-run wizard.</p>

<h3>4. Data subject rights</h3>
<p>Requests from data subjects (access, rectification, erasure,
portability) are handled by the Broker. The Supplier cannot action
subject-access requests because it holds no data.</p>

<h3>5. Breach notification</h3>
<p>If the Broker suffers a personal-data breach involving data held in
the Software, the Broker is the reporting party under UK GDPR Art. 33.
The Supplier will provide reasonable cooperation on request.</p>

<h3>6. Audit</h3>
<p>The Supplier will make available to the Broker, on written request,
such information as is necessary to demonstrate compliance with this
DPA.</p>

<h3>7. Term</h3>
<p>This DPA applies for the duration of the licence and ceases on
uninstallation.</p>

<p>Contact for data-protection questions:
<a href="mailto:{COMPANY_EMAIL_PRIVACY}">{COMPANY_EMAIL_PRIVACY}</a>.</p>

<p><i>This is a draft, provided for information only, and does not
constitute legal advice.</i></p>
"""
