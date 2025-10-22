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
from copy import deepcopy

from monitor_config import load_config

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
    """Runtime configuration settings for the parliament monitor."""

    def __init__(self, data: Dict[str, any]):
        self.raw = deepcopy(data)

        db_cfg = data.get("database", {})
        self.DB_PATH = db_cfg.get("path", "tasmania_parliament.db")

        source_cfg = data.get("sources", {}).get("urls", {})
        self.URLS = deepcopy(source_cfg)

        scraping_cfg = data.get("scraping", {})
        self.REQUEST_TIMEOUT = scraping_cfg.get("timeout", 30)
        self.RETRY_ATTEMPTS = scraping_cfg.get("retry_attempts", 3)
        self.RETRY_DELAY = scraping_cfg.get("retry_delay", 5)
        self.USER_AGENT = scraping_cfg.get("user_agent", "Tasmania Parliament Monitor Bot 1.0")

        monitoring_cfg = data.get("monitoring", {}).get("frequencies", {})
        default_frequencies = {
            "tabled_papers": 15,
            "members": 60,
            "committees": 30,
            "standing_orders": 120,
            "bills": 30,
            "hansard": 15,
        }
        self.CHECK_FREQUENCY = {**default_frequencies, **monitoring_cfg}

        email_cfg = data.get("notifications", {}).get("email", {})
        self.EMAIL_ENABLED = email_cfg.get("enabled", False)
        self.SMTP_SERVER = email_cfg.get("smtp_server", "smtp.gmail.com")
        self.SMTP_PORT = email_cfg.get("smtp_port", 587)
        self.EMAIL_FROM = email_cfg.get("from_address", "your-email@example.com")
        self.EMAIL_PASSWORD = email_cfg.get("password", "your-app-password")
        self.EMAIL_TO = email_cfg.get("recipients", ["recipient@example.com"])

        keyword_cfg = data.get("keywords", {})
        self.KEYWORDS_BY_CATEGORY = {
            category: sorted(set(words)) for category, words in keyword_cfg.items()
        }
        # Maintain backwards compatibility for existing logic
        self.ALERT_KEYWORDS = sorted(
            {word for words in self.KEYWORDS_BY_CATEGORY.values() for word in words}
        )

        alerts_cfg = data.get("alerts", {})
        self.CRITICAL_KEYWORDS = alerts_cfg.get("critical_keywords", [])
        self.HIGH_PRIORITY_SOURCES = alerts_cfg.get("high_priority_sources", [])

        dashboard_cfg = data.get("dashboard", {})
        self.DASHBOARD_REFRESH_SECONDS = dashboard_cfg.get("refresh_interval_seconds", 120)

    def to_dict(self) -> Dict[str, any]:
        """Return the raw configuration dictionary."""
        return deepcopy(self.raw)


def load_runtime_config() -> Config:
    """Load the runtime configuration from disk."""
    return Config(load_config())


RUNTIME_CONFIG = load_runtime_config()


def refresh_runtime_config() -> Config:
    """Reload configuration from disk and update runtime defaults."""
    global RUNTIME_CONFIG
    RUNTIME_CONFIG = load_runtime_config()
    return RUNTIME_CONFIG


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

    def __init__(self, config: Config):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': self.config.USER_AGENT
        })
    
    def fetch_page(self, url: str, retry_count: int = 0) -> Optional[str]:
        """Fetch webpage content with retry logic"""
        try:
            response = self.session.get(url, timeout=self.config.REQUEST_TIMEOUT)
            response.raise_for_status()
            return response.text
        except requests.RequestException as e:
            logging.error(f"Error fetching {url}: {e}")
            if retry_count < self.config.RETRY_ATTEMPTS:
                time.sleep(self.config.RETRY_DELAY)
                return self.fetch_page(url, retry_count + 1)
            return None

    def fetch_pdf(self, url: str) -> Optional[bytes]:
        """Fetch PDF content"""
        try:
            response = self.session.get(url, timeout=self.config.REQUEST_TIMEOUT)
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

    def __init__(self, config: Optional[Config] = None):
        self.config = config or refresh_runtime_config()
        self.db = DatabaseManager(self.config.DB_PATH)
        self.scraper = WebScraper(self.config)
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
        if not url:
            logging.warning(f"No tabled paper URL configured for {chamber}")
            return documents

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
        url = self.config.URLS.get('bills')
        if not url:
            logging.warning("Bills URL missing from configuration")
            return documents
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
        
        for key, url in self.config.URLS.items():
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
        for keyword in self.config.ALERT_KEYWORDS:
            if keyword.lower() in text_lower:
                keywords_found.append(keyword)
        
        doc.keywords_found = keywords_found
        
        # Determine alert level
        if any(kw.lower() in text_lower for kw in self.config.CRITICAL_KEYWORDS):
            doc.alert_level = AlertLevel.CRITICAL
        elif any(source.lower() in text_lower for source in self.config.HIGH_PRIORITY_SOURCES):
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
        if not self.config.EMAIL_ENABLED:
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
            msg['From'] = self.config.EMAIL_FROM
            msg['To'] = ', '.join(self.config.EMAIL_TO)

            msg.attach(MIMEText(html_content, 'html'))

            with smtplib.SMTP(self.config.SMTP_SERVER, self.config.SMTP_PORT) as server:
                server.starttls()
                server.login(self.config.EMAIL_FROM, self.config.EMAIL_PASSWORD)
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
            self.config.URLS.get('house_tabled'),
            'House of Assembly'
        )
        all_documents.extend(docs)

        # Scrape Legislative Council tabled papers
        docs = self.scrape_tabled_papers(
            self.config.URLS.get('lc_tabled'),
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
        schedule.every(self.config.CHECK_FREQUENCY['tabled_papers']).minutes.do(
            lambda: self.scrape_tabled_papers(self.config.URLS.get('house_tabled'), 'House of Assembly')
        )
        schedule.every(self.config.CHECK_FREQUENCY['bills']).minutes.do(
            self.scrape_bills
        )
        schedule.every(self.config.CHECK_FREQUENCY['committees']).minutes.do(
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
                    'keywords_tracked': len(self.config.ALERT_KEYWORDS)
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
