#!/usr/bin/env python3
"""
Tasmania Parliament Monitor - API Server
Provides REST API for dashboard and external integrations
"""

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
import sqlite3
import json
from datetime import datetime, timedelta
import os
from pathlib import Path

from monitor_config import (
    load_config as load_monitor_config,
    save_config as save_monitor_config,
    get_dashboard_logic,
    CONFIG_PATH,
)

# Import the parliament monitor for syncing operations
try:
    from parliament_monitor import ParliamentMonitor, refresh_runtime_config
except Exception:
    # Avoid import errors when the monitor is not yet initialised during tests
    ParliamentMonitor = None
    refresh_runtime_config = None

# Initialize Flask app
app = Flask(__name__)
CORS(app)  # Enable CORS for dashboard access

# Configuration
config = load_monitor_config()
DB_PATH = config.get('database', {}).get('path', 'tasmania_parliament.db')


def reload_configuration():
    """Reload configuration from disk and refresh monitor cache."""
    global config, DB_PATH
    config = load_monitor_config()
    DB_PATH = config.get('database', {}).get('path', 'tasmania_parliament.db')
    if refresh_runtime_config:
        refresh_runtime_config()

# Database helper functions
def get_db_connection():
    """Create database connection"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def query_db(query, args=(), one=False):
    """Execute database query"""
    conn = get_db_connection()
    cursor = conn.execute(query, args)
    result = cursor.fetchall()
    conn.close()
    return (result[0] if result else None) if one else result

# API Routes

@app.route('/')
def index():
    """Serve the dashboard HTML"""
    dashboard_path = Path('tasmania_parliament_dashboard.html')
    if dashboard_path.exists():
        return send_file(dashboard_path)
    return jsonify({"error": "Dashboard not found"}), 404

@app.route('/api/status')
def api_status():
    """API health check"""
    return jsonify({
        "status": "online",
        "timestamp": datetime.now().isoformat(),
        "database": os.path.exists(DB_PATH),
        "version": "1.0.0"
    })

@app.route('/api/stats')
def api_stats():
    """Get dashboard statistics"""
    try:
        # Documents today
        today = datetime.now().date()
        docs_today = query_db(
            "SELECT COUNT(*) as count FROM documents WHERE DATE(date_discovered) = ?",
            (today,), one=True
        )
        
        # Active alerts
        alerts = query_db(
            """SELECT alert_level, COUNT(*) as count 
               FROM alerts 
               WHERE sent = 0 
               GROUP BY alert_level"""
        )
        
        # Total watching
        # Count keywords across all categories in config
        keywords_cfg = config.get('keywords', {}) or {}
        keywords_count = sum(len(words) for words in keywords_cfg.values())

        # Count members from the database
        members_count_row = query_db(
            "SELECT COUNT(*) as count FROM members",
            (),
            one=True
        )
        members_count = members_count_row['count'] if members_count_row else 0

        # Count committees (active or inquiry) from the database
        committees_count_row = query_db(
            "SELECT COUNT(*) as count FROM committees WHERE status IN ('active','inquiry')",
            (),
            one=True
        )
        committees_count = committees_count_row['count'] if committees_count_row else 0

        return jsonify({
            "new_today": docs_today['count'] if docs_today else 0,
            "active_alerts": {
                alert['alert_level']: alert['count'] 
                for alert in alerts
            } if alerts else {},
            "total_alerts": sum(alert['count'] for alert in alerts) if alerts else 0,
            "watching": {
                "keywords": keywords_count,
                "members": members_count,
                "committees": committees_count
            }
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/documents')
def api_documents():
    """Get recent documents"""
    try:
        # Get query parameters
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)
        doc_type = request.args.get('type')
        chamber = request.args.get('chamber')
        days = request.args.get('days', 7, type=int)
        
        # Build query
        query = "SELECT * FROM documents WHERE 1=1"
        params = []
        
        if doc_type:
            query += " AND document_type = ?"
            params.append(doc_type)
        
        if chamber:
            query += " AND chamber = ?"
            params.append(chamber)
        
        if days:
            cutoff = datetime.now() - timedelta(days=days)
            query += " AND date_discovered >= ?"
            params.append(cutoff)
        
        query += " ORDER BY date_discovered DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        # Execute query
        documents = query_db(query, params)
        
        # Format response
        result = []
        for doc in documents:
            result.append({
                "id": doc['id'],
                "title": doc['title'],
                "description": doc['description'],
                "type": doc['document_type'],
                "chamber": doc['chamber'],
                "date_published": doc['date_published'],
                "date_discovered": doc['date_discovered'],
                "url": doc['document_url'] or doc['source_url'],
                "member": doc['member'],
                "committee": doc['committee'],
                "portfolio": doc['portfolio'],
                "keywords": json.loads(doc['keywords_found'] or '[]'),
                "alert_level": doc['alert_level']
            })
        
        return jsonify({
            "documents": result,
            "count": len(result),
            "offset": offset,
            "limit": limit
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/alerts')
def api_alerts():
    """Get active alerts"""
    try:
        # Get query parameters
        level = request.args.get('level')
        sent = request.args.get('sent', type=bool)
        
        # Build query
        query = """
            SELECT a.*, d.title as doc_title, d.document_url, d.chamber
            FROM alerts a
            JOIN documents d ON a.document_id = d.id
            WHERE 1=1
        """
        params = []
        
        if level:
            query += " AND a.alert_level = ?"
            params.append(level)
        
        if sent is not None:
            query += " AND a.sent = ?"
            params.append(1 if sent else 0)
        
        query += " ORDER BY a.date_created DESC LIMIT 100"
        
        # Execute query
        alerts = query_db(query, params)
        
        # Format response
        result = []
        for alert in alerts:
            result.append({
                "id": alert['id'],
                "document_id": alert['document_id'],
                "level": alert['alert_level'],
                "title": alert['title'] or alert['doc_title'],
                "description": alert['description'],
                "keywords_matched": alert['keywords_matched'],
                "date_created": alert['date_created'],
                "sent": bool(alert['sent']),
                "document_url": alert['document_url'],
                "chamber": alert['chamber']
            })
        
        return jsonify({
            "alerts": result,
            "count": len(result),
            "critical": sum(1 for a in result if a['level'] == 'critical'),
            "high": sum(1 for a in result if a['level'] == 'high'),
            "standard": sum(1 for a in result if a['level'] == 'standard')
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/feed')
def api_feed():
    """Get activity feed items"""
    try:
        # Get recent documents with alerts
        query = """
            SELECT 
                d.*,
                a.alert_level as alert_priority
            FROM documents d
            LEFT JOIN alerts a ON d.id = a.document_id
            ORDER BY d.date_discovered DESC
            LIMIT 20
        """
        
        items = query_db(query)
        
        # Format feed items
        feed = []
        for item in items:
            # Calculate time ago
            discovered = datetime.fromisoformat(item['date_discovered'])
            now = datetime.now()
            diff = now - discovered
            
            if diff.days == 0:
                if diff.seconds < 3600:
                    time_str = f"{diff.seconds // 60} minutes ago"
                else:
                    time_str = f"{diff.seconds // 3600} hours ago"
            elif diff.days == 1:
                time_str = "Yesterday"
            else:
                time_str = f"{diff.days} days ago"
            
            feed.append({
                "id": item['id'],
                "type": item['alert_level'] or 'standard',
                "title": item['title'],
                "description": item['description'] or 'No description available',
                "time": time_str,
                "keywords": json.loads(item['keywords_found'] or '[]'),
                "chamber": item['chamber'] or 'Unknown',
                "document": f"{item['document_type']}-{item['id']}"
            })
        
        return jsonify(feed)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/members')
def api_members():
    """Get parliament members"""
    try:
        members = query_db(
            """SELECT * FROM members 
               ORDER BY chamber, name
               LIMIT 100"""
        )
        
        result = []
        for member in members:
            result.append({
                "id": member['id'],
                "name": member['name'],
                "role": member['role'],
                "party": member['party'],
                "chamber": member['chamber'],
                "electorate": member['electorate'],
                "portfolios": json.loads(member['portfolios'] or '[]'),
                "committees": json.loads(member['committees'] or '[]')
            })
        
        return jsonify({
            "members": result,
            "count": len(result)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/committees')
def api_committees():
    """Get committees information"""
    try:
        committees = query_db(
            """SELECT * FROM committees 
               WHERE status IN ('active', 'inquiry')
               ORDER BY chamber, name"""
        )
        
        result = []
        for committee in committees:
            result.append({
                "id": committee['id'],
                "name": committee['name'],
                "type": committee['type'],
                "chamber": committee['chamber'],
                "status": committee['status'],
                "description": committee['description'],
                "chair": committee['chair'],
                "members": json.loads(committee['members'] or '[]'),
                "inquiries": json.loads(committee['current_inquiries'] or '[]')
            })
        
        return jsonify({
            "committees": result,
            "count": len(result),
            "active": sum(1 for c in result if c['status'] == 'active'),
            "inquiries": sum(1 for c in result if c['status'] == 'inquiry')
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/keywords')
def api_keywords():
    """Get tracked keywords"""
    try:
        keywords = config.get('keywords', {})
        
        # Flatten keywords
        all_keywords = []
        for category, words in keywords.items():
            for word in words:
                all_keywords.append({
                    "keyword": word,
                    "category": category
                })
        
        return jsonify({
            "keywords": all_keywords,
            "categories": list(keywords.keys()),
            "count": len(all_keywords)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/ui-model')
def api_ui_model():
    """Expose dashboard logic progression rules and section metadata."""
    try:
        dashboard_cfg = config.get('dashboard', {})
        logic = get_dashboard_logic(config)
        refresh_seconds = dashboard_cfg.get('refresh_interval_seconds', 120)

        sections = [
            {
                "id": "overview",
                "label": "Overview",
                "endpoint": "/api/stats",
                "description": "Headline metrics summarising monitoring performance",
                "refresh_seconds": refresh_seconds,
            },
            {
                "id": "documents",
                "label": "Documents",
                "endpoint": "/api/documents",
                "description": "Latest tabled papers, bills, and committee publications",
                "refresh_seconds": refresh_seconds,
            },
            {
                "id": "alerts",
                "label": "Alerts",
                "endpoint": "/api/alerts",
                "description": "Priority-ranked alerts generated from keyword analysis",
                "refresh_seconds": refresh_seconds,
            },
            {
                "id": "members",
                "label": "Members",
                "endpoint": "/api/members",
                "description": "Member profiles with roles, committees, and portfolios",
                "refresh_seconds": refresh_seconds * 4,
            },
            {
                "id": "committees",
                "label": "Committees",
                "endpoint": "/api/committees",
                "description": "Inquiry status, membership, and submission deadlines",
                "refresh_seconds": refresh_seconds * 2,
            },
            {
                "id": "watchlist",
                "label": "Watchlist",
                "endpoint": "/api/keywords",
                "description": "Managed keyword library grouped by strategic theme",
                "refresh_seconds": refresh_seconds * 6,
            },
            {
                "id": "reports",
                "label": "Reports",
                "endpoint": "/api/export",
                "description": "Historical exports and scheduled reporting hooks",
                "refresh_seconds": refresh_seconds * 6,
            },
        ]

        return jsonify({
            "refresh_seconds": refresh_seconds,
            "logic": logic,
            "sections": sections,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

@app.route('/api/keywords', methods=['POST'])
def api_add_keyword():
    """Add new keyword to tracking"""
    try:
        data = request.json
        keyword = data.get('keyword')
        category = data.get('category', 'custom')
        
        if not keyword:
            return jsonify({"error": "Keyword required"}), 400
        
        current_cfg = load_monitor_config()
        keywords_cfg = current_cfg.setdefault('keywords', {})

        if category not in keywords_cfg:
            keywords_cfg[category] = []

        if keyword in keywords_cfg[category]:
            return jsonify({
                "success": False,
                "message": "Keyword already exists"
            }), 409

        keywords_cfg[category].append(keyword)
        save_monitor_config(current_cfg)
        reload_configuration()

        return jsonify({
            "success": True,
            "keyword": keyword,
            "category": category
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/keywords', methods=['DELETE'])
def api_delete_keyword():
    """Remove keyword from tracking"""
    try:
        data = request.json
        if not data:
            return jsonify({"error": "JSON body required"}), 400
        keyword = data.get('keyword')
        category = data.get('category')
        if not keyword:
            return jsonify({"error": "Keyword required"}), 400

        if not os.path.exists(CONFIG_PATH):
            return jsonify({"error": "Configuration not found"}), 500
        cfg = load_monitor_config()

        # Remove keyword
        removed = False
        for cat, words in cfg.get('keywords', {}).items():
            if category and cat != category:
                continue
            if keyword in words:
                words.remove(keyword)
                removed = True

        if removed:
            save_monitor_config(cfg)
            reload_configuration()
            return jsonify({"success": True, "keyword": keyword, "category": category or 'any'}), 200
        else:
            return jsonify({"success": False, "message": "Keyword not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/sync', methods=['POST'])
def api_sync():
    """Trigger a monitoring cycle to fetch new documents"""
    try:
        if ParliamentMonitor is None:
            return jsonify({"error": "Monitoring service not available"}), 500
        monitor = ParliamentMonitor()
        new_docs = monitor.run_monitoring_cycle()
        return jsonify({
            "success": True,
            "new_documents": len(new_docs)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/search')
def api_search():
    """Search documents"""
    try:
        query_text = request.args.get('q', '')
        
        if not query_text:
            return jsonify({"error": "Query required"}), 400
        
        # Search in title, description, and content
        documents = query_db(
            """SELECT * FROM documents 
               WHERE title LIKE ? 
               OR description LIKE ? 
               OR content_text LIKE ?
               ORDER BY date_discovered DESC
               LIMIT 50""",
            (f'%{query_text}%', f'%{query_text}%', f'%{query_text}%')
        )
        
        result = []
        for doc in documents:
            result.append({
                "id": doc['id'],
                "title": doc['title'],
                "description": doc['description'],
                "type": doc['document_type'],
                "chamber": doc['chamber'],
                "date": doc['date_published'] or doc['date_discovered'],
                "url": doc['document_url'] or doc['source_url']
            })
        
        return jsonify({
            "results": result,
            "count": len(result),
            "query": query_text
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/trends')
def api_trends():
    """Get keyword and document trends"""
    try:
        # Get document counts by day for last 7 days
        query = """
            SELECT 
                DATE(date_discovered) as date,
                COUNT(*) as count,
                document_type
            FROM documents
            WHERE date_discovered >= date('now', '-7 days')
            GROUP BY DATE(date_discovered), document_type
            ORDER BY date DESC
        """
        
        daily_docs = query_db(query)
        
        # Get keyword hits
        keyword_query = """
            SELECT 
                keywords_found,
                COUNT(*) as count
            FROM documents
            WHERE keywords_found IS NOT NULL 
            AND keywords_found != '[]'
            AND date_discovered >= date('now', '-30 days')
        """
        
        keyword_hits = query_db(keyword_query)
        
        # Process keyword data
        keyword_counts = {}
        for row in keyword_hits:
            keywords = json.loads(row['keywords_found'] or '[]')
            for kw in keywords:
                keyword_counts[kw] = keyword_counts.get(kw, 0) + 1
        
        # Format response
        dates = {}
        for row in daily_docs:
            date = row['date']
            if date not in dates:
                dates[date] = {'total': 0, 'types': {}}
            dates[date]['total'] += row['count']
            dates[date]['types'][row['document_type']] = row['count']
        
        return jsonify({
            "daily": dates,
            "keywords": keyword_counts,
            "top_keywords": sorted(keyword_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/export')
def api_export():
    """Export data in various formats"""
    try:
        format_type = request.args.get('format', 'json')
        days = request.args.get('days', 30, type=int)
        
        # Get data
        cutoff = datetime.now() - timedelta(days=days)
        documents = query_db(
            "SELECT * FROM documents WHERE date_discovered >= ? ORDER BY date_discovered DESC",
            (cutoff,)
        )
        
        if format_type == 'json':
            result = []
            for doc in documents:
                result.append({
                    "title": doc['title'],
                    "description": doc['description'],
                    "type": doc['document_type'],
                    "chamber": doc['chamber'],
                    "date": doc['date_published'] or doc['date_discovered'],
                    "keywords": json.loads(doc['keywords_found'] or '[]'),
                    "alert_level": doc['alert_level'],
                    "url": doc['document_url'] or doc['source_url']
                })
            
            return jsonify({
                "export_date": datetime.now().isoformat(),
                "days_included": days,
                "document_count": len(result),
                "documents": result
            })
        
        elif format_type == 'csv':
            import csv
            import io
            
            output = io.StringIO()
            writer = csv.writer(output)
            
            # Write headers
            writer.writerow([
                'Title', 'Description', 'Type', 'Chamber', 
                'Date', 'Keywords', 'Alert Level', 'URL'
            ])
            
            # Write data
            for doc in documents:
                writer.writerow([
                    doc['title'],
                    doc['description'],
                    doc['document_type'],
                    doc['chamber'],
                    doc['date_published'] or doc['date_discovered'],
                    ', '.join(json.loads(doc['keywords_found'] or '[]')),
                    doc['alert_level'],
                    doc['document_url'] or doc['source_url']
                ])
            
            output.seek(0)
            return output.getvalue(), 200, {
                'Content-Type': 'text/csv',
                'Content-Disposition': f'attachment; filename=parliament_export_{datetime.now().strftime("%Y%m%d")}.csv'
            }
        
        else:
            return jsonify({"error": "Invalid format. Use 'json' or 'csv'"}), 400
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/report')
def api_report():
    """Generate summary report"""
    try:
        # Get report parameters
        period = request.args.get('period', 'week')
        
        if period == 'week':
            cutoff = datetime.now() - timedelta(days=7)
        elif period == 'month':
            cutoff = datetime.now() - timedelta(days=30)
        else:
            cutoff = datetime.now() - timedelta(days=1)
        
        # Get statistics
        stats = {
            "period": period,
            "generated": datetime.now().isoformat(),
            "documents": query_db(
                "SELECT COUNT(*) as count FROM documents WHERE date_discovered >= ?",
                (cutoff,), one=True
            )['count'],
            "alerts": query_db(
                "SELECT COUNT(*) as count FROM alerts WHERE date_created >= ?",
                (cutoff,), one=True
            )['count'],
            "by_type": {},
            "by_chamber": {},
            "top_keywords": []
        }
        
        # Documents by type
        type_counts = query_db(
            """SELECT document_type, COUNT(*) as count 
               FROM documents 
               WHERE date_discovered >= ?
               GROUP BY document_type""",
            (cutoff,)
        )
        for row in type_counts:
            stats['by_type'][row['document_type']] = row['count']
        
        # Documents by chamber
        chamber_counts = query_db(
            """SELECT chamber, COUNT(*) as count 
               FROM documents 
               WHERE date_discovered >= ? AND chamber IS NOT NULL
               GROUP BY chamber""",
            (cutoff,)
        )
        for row in chamber_counts:
            stats['by_chamber'][row['chamber']] = row['count']
        
        # Top keywords
        keyword_data = query_db(
            """SELECT keywords_found 
               FROM documents 
               WHERE date_discovered >= ? AND keywords_found IS NOT NULL""",
            (cutoff,)
        )
        
        keyword_counts = {}
        for row in keyword_data:
            keywords = json.loads(row['keywords_found'] or '[]')
            for kw in keywords:
                keyword_counts[kw] = keyword_counts.get(kw, 0) + 1
        
        stats['top_keywords'] = sorted(
            keyword_counts.items(), 
            key=lambda x: x[1], 
            reverse=True
        )[:10]
        
        return jsonify(stats)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Internal server error"}), 500

# Main execution
if __name__ == '__main__':
    # Check if database exists
    if not os.path.exists(DB_PATH):
        print(f"Warning: Database '{DB_PATH}' not found.")
        print("Run 'python parliament_monitor.py --once' to initialize the database first.")
    
    # Get port from config or use default
    port = config.get('api', {}).get('port', 5000)
    
    print(f"Starting API server on http://localhost:{port}")
    print(f"Dashboard available at http://localhost:{port}/")
    print(f"API documentation at http://localhost:{port}/api/status")
    
    # Run the server
    app.run(
        host='0.0.0.0',
        port=port,
        debug=True  # Set to False in production
    )
