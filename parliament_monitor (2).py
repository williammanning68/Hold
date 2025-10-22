#!/usr/bin/env python3
"""
Tasmania Parliament Monitor - Backend Service
Monitors Tasmania Parliament website for new documents and updates
"""

import json
import hashlib
import logging
import sqlite3
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
import re
import time
from dataclasses import dataclass, asdict
from enum import Enum

# Note: In production, install these packages:
# pip install requests beautifulsoup4 pdfplumber schedule

try:
    import requests
    from bs4 import BeautifulSoup
    import pdfplumber
    import schedule
except ImportError:
    print("Please install required packages: pip install requests beautifulsoup4 pdfplumber schedule")

# Configuration
class Config:
    """Configuration settings for the parliament monitor"""
    
    # Database
    DB_PATH = "tasmania_parliament.db"
    
    # Monitoring URLs
    URLS = {
        "house_members": "https://www.parliament.tas.gov.au/house-of-assembly/house-members",
        "house_tabled": "https://www.parliament.tas.gov.au/house-of-assembly/tabled-papers-2025",
        "house_register": "https://www.parliament.tas.gov.au/house-of-assembly/register-of-members-interests",
        "lc_members": "https://www.parliament.tas.gov.au/legislative-council/current-members",
        "lc_tabled": "https://www.parliament.tas.gov.au/legislative-council/tpp",
        "lc_register": "https://www.parliament.tas.gov.au/legislative-council/register-of-members-interests",
        "committees_ha": "https://www.parliament.tas.gov.au/house-of-assembly/committees",
        "committees_lc": "https://www.parliament.tas.gov.au/legislative-council/committees",
        "committees_joint": "https://www.parliament.tas.gov.au/parliamentary-committees/current-committees",
        "standing_orders_ha": "https://www.parliament.tas.gov.au/house-of-assembly/standing-orders",
        "standing_orders_lc": "https://www.parliament.tas.gov.au/legislative-council/standing-orders",
        "bills": "https://www.parliament.tas.gov.au/bills/bills-by-year",
        "hansard": "https://www.parliament.tas.gov.au/hansard",
        "papers_search": "https://search.parliament.tas.gov.au"
    }
    
    # Scraping settings
    REQUEST_TIMEOUT = 30
    RETRY_ATTEMPTS = 3
    RETRY_DELAY = 5
    USER_AGENT = "Tasmania Parliament Monitor Bot 1.0"
    
    # Monitoring frequency (minutes)
    CHECK_FREQUENCY = {
        "tabled_papers": 15,
        "members": 60,
        "committees": 30,
        "standing_orders": 120,
        "bills": 30,
        "hansard": 15
    }
    
    # Email settings (configure with your SMTP server)
    EMAIL_ENABLED = False  # Set to True and configure below
    SMTP_SERVER = "smtp.gmail.com"
    SMTP_PORT = 587
    EMAIL_FROM = "your-email@example.com"
    EMAIL_PASSWORD = "your-app-password"
    EMAIL_TO = ["recipient@example.com"]
    
    # Alert settings
    ALERT_KEYWORDS = [
        # Gaming/Gambling
        "gaming", "casino", "wagering", "betting", "gambling",
        "lottery", "pokies", "electronic gaming",
        
        # Infrastructure
        "infrastructure", "construction", "roads", "bridges",
        "public works", "capital projects", "development",
        
        # Environment
        "environment", "climate", "emissions", "pollution",
        "conservation", "renewable", "sustainability", "waste",
        
        # Health
        "health", "hospital", "medical", "healthcare",
        "mental health", "aged care", "ambulance",
        
        # Business/Economy
        "business", "economy", "tax", "budget", "fiscal",
        "investment", "employment", "industry", "tourism",
        
        # Planning
        "planning", "zoning", "land use", "development",
        "heritage", "building", "subdivision",
        
        # Aboriginal Affairs
        "aboriginal", "indigenous", "reconciliation",
        "native title", "cultural heritage"
    ]
    
    # Alert priority levels
    CRITICAL_KEYWORDS = [
        "urgent", "immediate", "emergency", "crisis",
        "mandatory", "compliance", "penalty", "enforcement"
    ]
    
    HIGH_PRIORITY_SOURCES = [
        "Premier", "Treasurer", "Attorney-General",
        "Minister for Health", "Minister for Infrastructure"
    ]


class AlertLevel(Enum):
    """Alert priority levels"""
    CRITICAL = "critical"
    HIGH = "high"
    STANDARD = "standard"
    INFO = "info"


class DocumentType(Enum):
    """Types of parliamentary documents"""
    TABLED_PAPER = "tabled_paper"
    BILL = "bill"
    COMMITTEE_REPORT = "committee_report"
    HANSARD = "hansard"
    REGISTER = "register"
    STANDING_ORDER = "standing_order"
    OTHER = "other"


@dataclass
class Document:
    """Represents a parliamentary document"""
    id: Optional[int] = None
    source_url: str = ""
    document_url: Optional[str] = None
    title: str = ""
    description: Optional[str] = None
    document_type: DocumentType = DocumentType.OTHER
    chamber: Optional[str] = None
    date_published: Optional[datetime] = None
    date_discovered: datetime = None
    member: Optional[str] = None
    committee: Optional[str] = None
    portfolio: Optional[str] = None
    file_hash: Optional[str] = None
    content_text: Optional[str] = None
    keywords_found: List[str] = None
    alert_level: AlertLevel = AlertLevel.INFO
    processed: bool = False
    
    def __post_init__(self):
        if self.date_discovered is None:
            self.date_discovered = datetime.now()
        if self.keywords_found is None:
            self.keywords_found = []


class DatabaseManager:
    """Manages SQLite database operations"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize database tables"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_url TEXT NOT NULL,
                    document_url TEXT,
                    title TEXT NOT NULL,
                    description TEXT,
                    document_type TEXT,
                    chamber TEXT,
                    date_published TIMESTAMP,
                    date_discovered TIMESTAMP NOT NULL,
                    member TEXT,
                    committee TEXT,
                    portfolio TEXT,
                    file_hash TEXT UNIQUE,
                    content_text TEXT,
                    keywords_found TEXT,
                    alert_level TEXT,
                    processed BOOLEAN DEFAULT FALSE,
                    UNIQUE(source_url, title, date_published)
                )
            ''')
            
            conn.execute('''
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER,
                    alert_level TEXT,
                    title TEXT,
                    description TEXT,
                    keywords_matched TEXT,
                    date_created TIMESTAMP,
                    sent BOOLEAN DEFAULT FALSE,
                    FOREIGN KEY (document_id) REFERENCES documents(id)
                )
            ''')
            
            conn.execute('''
                CREATE TABLE IF NOT EXISTS members (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE,
                    role TEXT,
                    party TEXT,
                    chamber TEXT,
                    electorate TEXT,
                    portfolios TEXT,
                    committees TEXT,
                    last_updated TIMESTAMP
                )
            ''')
            
            conn.execute('''
                CREATE TABLE IF NOT EXISTS committees (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE,
                    type TEXT,
                    chamber TEXT,
                    status TEXT,
                    description TEXT,
                    chair TEXT,
                    members TEXT,
                    current_inquiries TEXT,
                    last_updated TIMESTAMP
                )
            ''')
            
            conn.execute('''
                CREATE TABLE IF NOT EXISTS scrape_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT,
                    success BOOLEAN,
                    documents_found INTEGER,
                    error_message TEXT,
                    timestamp TIMESTAMP
                )
            ''')
    
    def save_document(self, doc: Document) -> Optional[int]:
        """Save document to database"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute('''
                    INSERT OR IGNORE INTO documents (
                        source_url, document_url, title, description,
                        document_type, chamber, date_published, date_discovered,
                        member, committee, portfolio, file_hash, content_text,
                        keywords_found, alert_level, processed
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    doc.source_url, doc.document_url, doc.title, doc.description,
                    doc.document_type.value, doc.chamber, doc.date_published,
                    doc.date_discovered, doc.member, doc.committee, doc.portfolio,
                    doc.file_hash, doc.content_text,
                    json.dumps(doc.keywords_found), doc.alert_level.value,
                    doc.processed
                ))
                return cursor.lastrowid
        except sqlite3.Error as e:
            logging.error(f"Database error saving document: {e}")
            return None
    
    def get_unprocessed_documents(self) -> List[Document]:
        """Get documents that haven't been processed"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute('''
                SELECT * FROM documents WHERE processed = FALSE
            ''').fetchall()
            
            documents = []
            for row in rows:
                doc = Document(
                    id=row['id'],
                    source_url=row['source_url'],
                    document_url=row['document_url'],
                    title=row['title'],
                    description=row['description'],
                    document_type=DocumentType(row['document_type']),
                    chamber=row['chamber'],
                    date_published=row['date_published'],
                    date_discovered=row['date_discovered'],
                    member=row['member'],
                    committee=row['committee'],
                    portfolio=row['portfolio'],
                    file_hash=row['file_hash'],
                    content_text=row['content_text'],
                    keywords_found=json.loads(row['keywords_found'] or '[]'),
                    alert_level=AlertLevel(row['alert_level']),
                    processed=bool(row['processed'])
                )
                documents.append(doc)
            
            return documents
    
    def mark_processed(self, doc_id: int):
        """Mark document as processed"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                'UPDATE documents SET processed = TRUE WHERE id = ?',
                (doc_id,)
            )
    
    def document_exists(self, file_hash: str) -> bool:
        """Check if document already exists by hash"""
        with sqlite3.connect(self.db_path) as conn:
            result = conn.execute(
                'SELECT COUNT(*) FROM documents WHERE file_hash = ?',
                (file_hash,)
            ).fetchone()
            return result[0] > 0


class WebScraper:
    """Handles web scraping operations"""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': Config.USER_AGENT
        })
    
    def fetch_page(self, url: str, retry_count: int = 0) -> Optional[str]:
        """Fetch webpage content with retry logic"""
        try:
            response = self.session.get(url, timeout=Config.REQUEST_TIMEOUT)
            response.raise_for_status()
            return response.text
        except requests.RequestException as e:
            logging.error(f"Error fetching {url}: {e}")
            if retry_count < Config.RETRY_ATTEMPTS:
                time.sleep(Config.RETRY_DELAY)
                return self.fetch_page(url, retry_count + 1)
            return None
    
    def fetch_pdf(self, url: str) -> Optional[bytes]:
        """Fetch PDF content"""
        try:
            response = self.session.get(url, timeout=Config.REQUEST_TIMEOUT)
            response.raise_for_status()
            if 'application/pdf' in response.headers.get('Content-Type', ''):
                return response.content
            return None
        except requests.RequestException as e:
            logging.error(f"Error fetching PDF {url}: {e}")
            return None
    
    def extract_pdf_text(self, pdf_content: bytes) -> Optional[str]:
        """Extract text from PDF content"""
        try:
            import io
            with pdfplumber.open(io.BytesIO(pdf_content)) as pdf:
                text = ''
                for page in pdf.pages:
                    text += page.extract_text() + '\n'
                return text
        except Exception as e:
            logging.error(f"Error extracting PDF text: {e}")
            return None


class ParliamentMonitor:
    """Main monitoring service"""
    
    def __init__(self):
        self.db = DatabaseManager(Config.DB_PATH)
        self.scraper = WebScraper()
        self.setup_logging()
    
    def setup_logging(self):
        """Configure logging"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('parliament_monitor.log'),
                logging.StreamHandler()
            ]
        )
    
    def scrape_tabled_papers(self, url: str, chamber: str) -> List[Document]:
        """Scrape tabled papers from a chamber page"""
        documents = []
        html = self.scraper.fetch_page(url)
        
        if not html:
            return documents
        
        soup = BeautifulSoup(html, 'html.parser')
        
        # Look for paper listings (adapt selectors based on actual HTML structure)
        papers = soup.find_all(['tr', 'div', 'li'], class_=re.compile(
            r'(paper|document|tabled)', re.I
        ))
        
        for paper in papers:
            try:
                # Extract title
                title_elem = paper.find(['a', 'span'], class_=re.compile(r'title', re.I))
                if not title_elem:
                    title_elem = paper.find('a')
                
                if not title_elem:
                    continue
                
                title = title_elem.get_text(strip=True)
                
                # Extract link
                link = None
                if title_elem.name == 'a':
                    link = title_elem.get('href')
                    if link and not link.startswith('http'):
                        link = f"https://www.parliament.tas.gov.au{link}"
                
                # Extract date
                date_text = paper.find(text=re.compile(r'\d{1,2}[\s/]\w+[\s/]\d{4}'))
                date_published = None
                if date_text:
                    try:
                        date_published = datetime.strptime(date_text, '%d %B %Y')
                    except:
                        pass
                
                # Create document
                doc = Document(
                    source_url=url,
                    document_url=link,
                    title=title,
                    document_type=DocumentType.TABLED_PAPER,
                    chamber=chamber,
                    date_published=date_published
                )
                
                # Generate hash
                hash_content = f"{title}{chamber}{date_published}"
                doc.file_hash = hashlib.sha256(hash_content.encode()).hexdigest()
                
                documents.append(doc)
                
            except Exception as e:
                logging.error(f"Error parsing paper: {e}")
        
        logging.info(f"Found {len(documents)} papers from {chamber}")
        return documents
    
    def scrape_bills(self) -> List[Document]:
        """Scrape current bills"""
        documents = []
        url = Config.URLS['bills']
        html = self.scraper.fetch_page(url)
        
        if not html:
            return documents
        
        soup = BeautifulSoup(html, 'html.parser')
        
        # Look for bill listings
        bills = soup.find_all(['tr', 'div'], class_=re.compile(r'bill', re.I))
        
        for bill in bills:
            try:
                title_elem = bill.find('a')
                if not title_elem:
                    continue
                
                title = title_elem.get_text(strip=True)
                link = title_elem.get('href')
                
                if link and not link.startswith('http'):
                    link = f"https://www.parliament.tas.gov.au{link}"
                
                # Extract status
                status = bill.find(text=re.compile(
                    r'(first|second|third) reading|royal assent', re.I
                ))
                
                doc = Document(
                    source_url=url,
                    document_url=link,
                    title=title,
                    description=status,
                    document_type=DocumentType.BILL
                )
                
                doc.file_hash = hashlib.sha256(title.encode()).hexdigest()
                documents.append(doc)
                
            except Exception as e:
                logging.error(f"Error parsing bill: {e}")
        
        logging.info(f"Found {len(documents)} bills")
        return documents
    
    def scrape_committees(self) -> List[Document]:
        """Scrape committee information"""
        documents = []
        
        for key, url in Config.URLS.items():
            if 'committee' not in key:
                continue
            
            html = self.scraper.fetch_page(url)
            if not html:
                continue
            
            soup = BeautifulSoup(html, 'html.parser')
            
            # Look for committee information
            committees = soup.find_all(['div', 'section'], class_=re.compile(
                r'committee', re.I
            ))
            
            for committee in committees:
                try:
                    name_elem = committee.find(['h2', 'h3', 'h4'])
                    if not name_elem:
                        continue
                    
                    name = name_elem.get_text(strip=True)
                    
                    # Look for inquiry information
                    inquiry = committee.find(text=re.compile(r'inquiry|submission', re.I))
                    
                    if inquiry:
                        doc = Document(
                            source_url=url,
                            title=f"Committee Update: {name}",
                            description=inquiry,
                            document_type=DocumentType.COMMITTEE_REPORT,
                            committee=name
                        )
                        
                        doc.file_hash = hashlib.sha256(
                            f"{name}{inquiry}".encode()
                        ).hexdigest()
                        
                        documents.append(doc)
                        
                except Exception as e:
                    logging.error(f"Error parsing committee: {e}")
        
        logging.info(f"Found {len(documents)} committee updates")
        return documents
    
    def analyze_document(self, doc: Document) -> Document:
        """Analyze document for keywords and set alert level"""
        if not doc.content_text:
            # Try to fetch and extract content if we have a PDF URL
            if doc.document_url and doc.document_url.endswith('.pdf'):
                pdf_content = self.scraper.fetch_pdf(doc.document_url)
                if pdf_content:
                    doc.content_text = self.scraper.extract_pdf_text(pdf_content)
        
        # Search for keywords
        text_to_search = f"{doc.title} {doc.description or ''} {doc.content_text or ''}"
        text_lower = text_to_search.lower()
        
        keywords_found = []
        for keyword in Config.ALERT_KEYWORDS:
            if keyword.lower() in text_lower:
                keywords_found.append(keyword)
        
        doc.keywords_found = keywords_found
        
        # Determine alert level
        if any(kw.lower() in text_lower for kw in Config.CRITICAL_KEYWORDS):
            doc.alert_level = AlertLevel.CRITICAL
        elif any(source.lower() in text_lower for source in Config.HIGH_PRIORITY_SOURCES):
            doc.alert_level = AlertLevel.HIGH
        elif len(keywords_found) > 3:
            doc.alert_level = AlertLevel.HIGH
        elif keywords_found:
            doc.alert_level = AlertLevel.STANDARD
        else:
            doc.alert_level = AlertLevel.INFO
        
        return doc
    
    def create_alert(self, doc: Document) -> Dict:
        """Create alert from document"""
        alert = {
            'document_id': doc.id,
            'alert_level': doc.alert_level.value,
            'title': doc.title,
            'description': doc.description,
            'keywords_matched': ', '.join(doc.keywords_found),
            'date_created': datetime.now(),
            'chamber': doc.chamber,
            'document_type': doc.document_type.value,
            'url': doc.document_url or doc.source_url
        }
        return alert
    
    def send_email_alert(self, alerts: List[Dict]):
        """Send email alerts"""
        if not Config.EMAIL_ENABLED:
            logging.info("Email alerts disabled")
            return
        
        try:
            # Group alerts by level
            critical = [a for a in alerts if a['alert_level'] == 'critical']
            high = [a for a in alerts if a['alert_level'] == 'high']
            standard = [a for a in alerts if a['alert_level'] == 'standard']
            
            # Build email content
            subject = f"Parliament Monitor Alert - {len(critical)} Critical, {len(high)} High Priority"
            
            html_content = f"""
            <html>
            <body style="font-family: Arial, sans-serif;">
                <h2 style="color: #004d3d;">Tasmania Parliament Monitor Alert</h2>
                <p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
                
                {'<h3 style="color: red;">🚨 CRITICAL ALERTS</h3>' if critical else ''}
                {''.join(self._format_alert_html(a) for a in critical)}
                
                {'<h3 style="color: orange;">⚠️ HIGH PRIORITY</h3>' if high else ''}
                {''.join(self._format_alert_html(a) for a in high)}
                
                {'<h3 style="color: blue;">📋 STANDARD UPDATES</h3>' if standard else ''}
                {''.join(self._format_alert_html(a) for a in standard)}
                
                <hr>
                <p style="color: #666;">
                    <small>
                    This is an automated alert from Tasmania Parliament Monitor.<br>
                    To modify alert settings, please update your configuration.
                    </small>
                </p>
            </body>
            </html>
            """
            
            # Send email
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = Config.EMAIL_FROM
            msg['To'] = ', '.join(Config.EMAIL_TO)
            
            msg.attach(MIMEText(html_content, 'html'))
            
            with smtplib.SMTP(Config.SMTP_SERVER, Config.SMTP_PORT) as server:
                server.starttls()
                server.login(Config.EMAIL_FROM, Config.EMAIL_PASSWORD)
                server.send_message(msg)
            
            logging.info(f"Email alert sent with {len(alerts)} items")
            
        except Exception as e:
            logging.error(f"Failed to send email: {e}")
    
    def _format_alert_html(self, alert: Dict) -> str:
        """Format single alert as HTML"""
        return f"""
        <div style="margin: 20px 0; padding: 15px; border-left: 4px solid 
                    {'red' if alert['alert_level'] == 'critical' else 
                     'orange' if alert['alert_level'] == 'high' else 'blue'};">
            <h4 style="margin: 0 0 10px 0;">{alert['title']}</h4>
            <p style="color: #666; margin: 5px 0;">
                {alert['description'] or 'No description available'}
            </p>
            <p style="margin: 5px 0;">
                <strong>Type:</strong> {alert['document_type']}<br>
                <strong>Chamber:</strong> {alert['chamber'] or 'N/A'}<br>
                <strong>Keywords:</strong> {alert['keywords_matched'] or 'None'}
            </p>
            <p style="margin: 10px 0 0 0;">
                <a href="{alert['url']}" style="color: #004d3d;">View Document →</a>
            </p>
        </div>
        """
    
    def run_monitoring_cycle(self):
        """Run one monitoring cycle"""
        logging.info("Starting monitoring cycle...")
        
        all_documents = []
        
        # Scrape House of Assembly tabled papers
        docs = self.scrape_tabled_papers(
            Config.URLS['house_tabled'],
            'House of Assembly'
        )
        all_documents.extend(docs)
        
        # Scrape Legislative Council tabled papers
        docs = self.scrape_tabled_papers(
            Config.URLS['lc_tabled'],
            'Legislative Council'
        )
        all_documents.extend(docs)
        
        # Scrape bills
        docs = self.scrape_bills()
        all_documents.extend(docs)
        
        # Scrape committees
        docs = self.scrape_committees()
        all_documents.extend(docs)
        
        # Process new documents
        new_documents = []
        for doc in all_documents:
            if not self.db.document_exists(doc.file_hash):
                # Analyze document
                doc = self.analyze_document(doc)
                
                # Save to database
                doc.id = self.db.save_document(doc)
                
                if doc.id and doc.keywords_found:
                    new_documents.append(doc)
                    logging.info(f"New document: {doc.title} [{doc.alert_level.value}]")
        
        # Create and send alerts for new documents
        if new_documents:
            alerts = [self.create_alert(doc) for doc in new_documents]
            self.send_email_alert(alerts)
        
        logging.info(f"Monitoring cycle complete. Found {len(new_documents)} new relevant documents")
        
        return new_documents
    
    def run_scheduled(self):
        """Run monitoring on schedule"""
        logging.info("Starting scheduled monitoring service...")
        
        # Schedule different checks
        schedule.every(Config.CHECK_FREQUENCY['tabled_papers']).minutes.do(
            lambda: self.scrape_tabled_papers(Config.URLS['house_tabled'], 'House of Assembly')
        )
        schedule.every(Config.CHECK_FREQUENCY['bills']).minutes.do(
            self.scrape_bills
        )
        schedule.every(Config.CHECK_FREQUENCY['committees']).minutes.do(
            self.scrape_committees
        )
        
        # Run full cycle every hour
        schedule.every().hour.do(self.run_monitoring_cycle)
        
        # Keep running
        while True:
            schedule.run_pending()
            time.sleep(60)  # Check every minute
    
    def export_to_json(self, output_file: str = 'parliament_data.json'):
        """Export database to JSON for the frontend"""
        with sqlite3.connect(self.db.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            # Get recent documents
            documents = conn.execute('''
                SELECT * FROM documents 
                ORDER BY date_discovered DESC 
                LIMIT 100
            ''').fetchall()
            
            # Get alerts
            alerts = conn.execute('''
                SELECT * FROM alerts 
                WHERE sent = TRUE 
                ORDER BY date_created DESC 
                LIMIT 50
            ''').fetchall()
            
            # Convert to dict
            data = {
                'last_updated': datetime.now().isoformat(),
                'documents': [dict(d) for d in documents],
                'alerts': [dict(a) for a in alerts],
                'stats': {
                    'total_documents': len(documents),
                    'critical_alerts': sum(1 for a in alerts if dict(a)['alert_level'] == 'critical'),
                    'high_alerts': sum(1 for a in alerts if dict(a)['alert_level'] == 'high'),
                    'keywords_tracked': len(Config.ALERT_KEYWORDS)
                }
            }
            
            # Write to file
            with open(output_file, 'w') as f:
                json.dump(data, f, indent=2, default=str)
            
            logging.info(f"Exported data to {output_file}")


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Tasmania Parliament Monitor')
    parser.add_argument('--once', action='store_true', 
                       help='Run once and exit')
    parser.add_argument('--export', action='store_true',
                       help='Export data to JSON')
    parser.add_argument('--scheduled', action='store_true',
                       help='Run on schedule')
    
    args = parser.parse_args()
    
    monitor = ParliamentMonitor()
    
    if args.export:
        monitor.export_to_json()
    elif args.once:
        monitor.run_monitoring_cycle()
    elif args.scheduled:
        monitor.run_scheduled()
    else:
        # Default: run once
        monitor.run_monitoring_cycle()


if __name__ == '__main__':
    main()
