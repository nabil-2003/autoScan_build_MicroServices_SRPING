#!/usr/bin/env python3
"""
Automatic Docker Generator for Microservices
Scans Maven projects and generates Dockerfiles and docker-compose.yml automatically
Includes version consistency checking and alignment
"""

import os
import re
import sys
import platform
import argparse
import yaml
from pathlib import Path
from typing import Dict, List, Set, Optional, Tuple
from collections import defaultdict
import xml.etree.ElementTree as ET


def _normalize_path(path_str: str) -> Path:
    """Convert Git-Bash-style Unix paths (e.g. /c/Users/...) to proper
    Windows paths (C:\\Users\\...) when running on Windows, then resolve."""
    if platform.system() == 'Windows':
        m = re.match(r'^/([a-zA-Z])(/.*)?$', path_str)
        if m:
            drive = m.group(1).upper()
            rest = (m.group(2) or '').replace('/', '\\')
            path_str = f'{drive}:{rest}'
    return Path(path_str).resolve()

class VersionChecker:
    """Checks and aligns version consistency across services"""
    
    @staticmethod
    def extract_versions(pom_file: Path) -> Dict[str, str]:
        """Extract version information from a pom.xml file"""
        versions = {
            'spring_boot': 'N/A',
            'spring_cloud': 'N/A',
            'java': 'N/A'
        }
        
        try:
            content = pom_file.read_text(encoding='utf-8')
            
            # Extract Spring Boot version
            boot_match = re.search(
                r'<artifactId>spring-boot-starter-parent</artifactId>\s*<version>([^<]+)</version>',
                content, re.DOTALL
            )
            if boot_match:
                versions['spring_boot'] = boot_match.group(1)
            
            # Extract Spring Cloud version
            cloud_match = re.search(r'<spring-cloud\.version>([^<]+)</spring-cloud\.version>', content)
            if cloud_match:
                versions['spring_cloud'] = cloud_match.group(1)
            
            # Extract Java version
            java_match = re.search(r'<java\.version>([^<]+)</java\.version>', content)
            if java_match:
                versions['java'] = java_match.group(1)
                
        except Exception:
            pass
            
        return versions
    
    @staticmethod
    def check_consistency(services: Dict) -> Tuple[bool, Dict]:
        """Check if all services use consistent versions"""
        version_groups = {
            'spring_boot': defaultdict(list),
            'spring_cloud': defaultdict(list),
            'java': defaultdict(list)
        }
        
        for service_name, service_info in services.items():
            pom_file = service_info['path'] / 'pom.xml'
            if pom_file.exists():
                versions = VersionChecker.extract_versions(pom_file)
                version_groups['spring_boot'][versions['spring_boot']].append(service_name)
                version_groups['spring_cloud'][versions['spring_cloud']].append(service_name)
                version_groups['java'][versions['java']].append(service_name)
        
        # Check consistency
        consistent = (
            len(version_groups['spring_boot']) == 1 and
            len([v for v in version_groups['spring_cloud'].keys() if v != 'N/A']) <= 1 and
            len(version_groups['java']) == 1
        )
        
        return consistent, version_groups
    
    @staticmethod
    def determine_target_versions(version_groups: Dict) -> Dict[str, str]:
        """Determine the best target versions to use"""
        target = {}
        
        # For Spring Boot, use the latest version
        boot_versions = [v for v in version_groups['spring_boot'].keys() if v != 'N/A']
        if boot_versions:
            # Sort by version and take the highest
            target['spring_boot'] = sorted(boot_versions, key=lambda x: [int(n) for n in x.split('.')])[-1]
        
        # For Spring Cloud, use the most common non-N/A version or default
        cloud_versions = [v for v in version_groups['spring_cloud'].keys() if v != 'N/A']
        if cloud_versions:
            # Use most common
            target['spring_cloud'] = max(cloud_versions, key=lambda v: len(version_groups['spring_cloud'][v]))
        else:
            # Default for Spring Boot 3.2.x
            target['spring_cloud'] = '2023.0.0'
        
        # For Java, use the most common version
        java_versions = [v for v in version_groups['java'].keys() if v != 'N/A']
        if java_versions:
            target['java'] = max(java_versions, key=lambda v: len(version_groups['java'][v]))
        
        return target
    
    @staticmethod
    def align_versions(services: Dict, target_versions: Dict) -> int:
        """Align all services to target versions"""
        aligned_count = 0
        
        for service_name, service_info in services.items():
            pom_file = service_info['path'] / 'pom.xml'
            if pom_file.exists():
                if VersionChecker._align_pom_file(pom_file, target_versions):
                    aligned_count += 1
        
        return aligned_count
    
    @staticmethod
    def _align_pom_file(pom_file: Path, target_versions: Dict) -> bool:
        """Align versions in a single pom.xml file"""
        try:
            content = pom_file.read_text(encoding='utf-8')
            original_content = content
            
            # Update Spring Boot parent version
            if 'spring_boot' in target_versions:
                content = re.sub(
                    r'(<artifactId>spring-boot-starter-parent</artifactId>\s*<version>)[^<]+(</version>)',
                    rf'\g<1>{target_versions["spring_boot"]}\g<2>',
                    content,
                    count=1,
                    flags=re.DOTALL
                )
            
            # Update or add Spring Cloud version
            if 'spring_cloud' in target_versions:
                if '<spring-cloud.version>' in content:
                    content = re.sub(
                        r'<spring-cloud\.version>[^<]+</spring-cloud\.version>',
                        f'<spring-cloud.version>{target_versions["spring_cloud"]}</spring-cloud.version>',
                        content
                    )
                else:
                    # Add if missing and Spring Cloud is used
                    if 'spring-cloud' in content:
                        content = re.sub(
                            r'(<java\.version>[^<]+</java\.version>)',
                            rf'\g<1>\n        <spring-cloud.version>{target_versions["spring_cloud"]}</spring-cloud.version>',
                            content,
                            count=1
                        )
            
            # Update Java version
            if 'java' in target_versions:
                content = re.sub(
                    r'<java\.version>[^<]+</java\.version>',
                    f'<java.version>{target_versions["java"]}</java.version>',
                    content
                )
            
            if content != original_content:
                pom_file.write_text(content, encoding='utf-8')
                return True
                
        except Exception as e:
            print(f"    ⚠️  Error aligning {pom_file.name}: {e}")
        
        return False

class ProjectScanner:
    """Scans project structure and collects information"""
    
    # Dependency patterns for detection
    DEPENDENCY_PATTERNS = {
        'postgres': [
            r'org\.postgresql:postgresql',
            r'<artifactId>postgresql</artifactId>'
        ],
        'mysql': [
            r'mysql-connector',
            r'<artifactId>mysql-connector-java</artifactId>'
        ],
        'mongodb': [
            r'spring-boot-starter-data-mongodb',
            r'<artifactId>spring-boot-starter-data-mongodb</artifactId>'
        ],
        'redis': [
            r'spring-boot-starter-data-redis',
            r'<artifactId>spring-boot-starter-data-redis</artifactId>'
        ],
        'kafka': [
            r'spring-kafka',
            r'<artifactId>spring-kafka</artifactId>'
        ],
        'rabbitmq': [
            r'spring-boot-starter-amqp',
            r'<artifactId>spring-boot-starter-amqp</artifactId>'
        ],
        'eureka-server': [
            r'spring-cloud-starter-netflix-eureka-server',
            r'<artifactId>spring-cloud-starter-netflix-eureka-server</artifactId>'
        ],
        'eureka-client': [
            r'spring-cloud-starter-netflix-eureka-client',
            r'<artifactId>spring-cloud-starter-netflix-eureka-client</artifactId>'
        ],
        'keycloak': [
            r'keycloak',
            r'oauth2-resource-server'
        ]
    }
    
    # Database images and ports
    DB_IMAGES = {
        'postgres': ('postgres:15', 5432),
        'mysql': ('mysql:8', 3306),
        'mongodb': ('mongo:7', 27017),
        'redis': ('redis:7-alpine', 6379),
        'kafka': ('bitnami/kafka:latest', 9092),
        'rabbitmq': ('rabbitmq:3-management', 5672)
    }
    
    def __init__(self, project_root: str):
        self.root = Path(project_root).resolve()
        self.services = {}
        self.dependencies = set()
        
    def scan(self):
        """Main scan method"""
        print("🔍 Scanning project...")
        
        # Find parent pom.xml
        parent_pom = self.root / "pom.xml"
        
        if parent_pom.exists():
            # Case 1: Parent pom.xml exists (multi-module or single app)
            # Extract modules from parent POM
            modules = self.extract_modules(parent_pom)
            
            # Check if this is a single Spring Boot application
            if len(modules) == 0:
                print("📦 Detected single Spring Boot application")
                # Check if it's a runnable service
                service_info = self.scan_service_for_single_app(self.root.name, self.root)
                if service_info:
                    self.services[self.root.name] = service_info
                    return True

                # Not directly runnable – check whether this is an orphaned sub-module
                # (pom.xml has <parent> but no <modules>). If so, look for sibling
                # modules one level up that might be runnable services.
                try:
                    pom_content = parent_pom.read_text(encoding='utf-8')
                    is_sub_module = '<parent>' in pom_content
                except Exception:
                    pom_content = ''
                    is_sub_module = False

                if is_sub_module:
                    parent_dir = self.root.parent
                    sibling_dirs = sorted([
                        d for d in parent_dir.iterdir()
                        if d.is_dir()
                        and not d.name.startswith('.')
                        and (d / 'pom.xml').exists()
                        and d != self.root
                    ])
                    if sibling_dirs:
                        print(f"  ℹ️  '{self.root.name}' is a shared library sub-module.")
                        print(f"  🔍 Scanning {len(sibling_dirs)} sibling module(s) in '{parent_dir.name}/'...")
                        for sdir in sibling_dirs:
                            sinfo = self.scan_service(sdir.name, sdir)
                            if sinfo:
                                self.services[sdir.name] = sinfo
                        if self.services:
                            return True
                    print("❌ Not a runnable Spring Boot application")
                    print(f"  ℹ️  Tip: '{self.root.name}' appears to be a shared library/utility "
                          f"sub-module, not a deployable microservice.")
                    print( "  ℹ️  Please upload the parent project that contains all microservice "
                           "modules (the folder with the root pom.xml listing <modules>).")
                    return False

                print("❌ Not a runnable Spring Boot application")
                return False
            else:
                print(f"📦 Found {len(modules)} modules: {', '.join(modules)}")
                
                # Scan each module
                for module in modules:
                    module_path = self.root / module
                    if module_path.exists():
                        service_info = self.scan_service(module, module_path)
                        if service_info:
                            self.services[module] = service_info
        else:
            # Case 2: No parent pom.xml - scan subdirectories for individual services
            print("📦 No parent pom.xml found - scanning subdirectories for individual services...")
            services_found = self.scan_subdirectories()
            
            if not services_found:
                print("❌ No services found in subdirectories")
                return False
                
        return len(self.services) > 0
    
    def scan_subdirectories(self) -> bool:
        """Scan subdirectories for individual Maven projects"""
        found = False
        
        # Get all subdirectories
        subdirs = [d for d in self.root.iterdir() if d.is_dir() and not d.name.startswith('.')]
        
        # Filter directories that contain pom.xml
        service_dirs = []
        for subdir in subdirs:
            pom_file = subdir / "pom.xml"
            if pom_file.exists():
                service_dirs.append(subdir)
        
        if not service_dirs:
            return False
        
        print(f"📦 Found {len(service_dirs)} services with pom.xml files")
        
        # Scan each service directory
        for service_dir in service_dirs:
            service_name = service_dir.name
            service_info = self.scan_service(service_name, service_dir)
            if service_info:
                self.services[service_name] = service_info
                found = True
        
        return found
    
    def extract_modules(self, pom_path: Path) -> List[str]:
        """Extract module names from parent pom.xml"""
        try:
            tree = ET.parse(pom_path)
            root = tree.getroot()
            
            # Handle XML namespace
            ns = {'maven': 'http://maven.apache.org/POM/4.0.0'}
            modules = root.findall('.//maven:module', ns)
            
            # Try without namespace if not found
            if not modules:
                modules = root.findall('.//module')
                
            return [m.text.strip() for m in modules if m.text]
        except Exception as e:
            print(f"⚠️  Error parsing parent POM: {e}")
            return []
    
    def scan_service(self, name: str, path: Path) -> Optional[Dict]:
        """Scan a single service/module"""
        print(f"  📂 Scanning {name}...")
        
        service_info = {
            'name': name,
            'path': path,
            'port': 8080,
            'dependencies': set(),
            'has_dockerfile': False,
            'artifact_id': name,
            'is_service': True
        }
        
        # Check for existing Dockerfile
        dockerfile = path / "Dockerfile"
        service_info['has_dockerfile'] = dockerfile.exists()
        
        # Parse pom.xml
        pom_file = path / "pom.xml"
        if pom_file.exists():
            pom_info = self.parse_pom(pom_file)
            service_info.update(pom_info)
            service_info['dependencies'].update(pom_info.get('dependencies', set()))
        
        # Parse application configuration
        config_info = self.parse_config(path)
        if config_info:
            service_info.update(config_info)
        
        # Skip if this is not an actual service (e.g., common/shared library)
        # Services should have application.yml/properties or spring-boot-maven-plugin
        if not self.is_runnable_service(service_info, path):
            print(f"    ℹ️  Skipping {name} (appears to be a library, not a service)")
            return None
            
        # Update global dependencies
        self.dependencies.update(service_info['dependencies'])
        
        return service_info
    
    def scan_service_for_single_app(self, name: str, path: Path) -> Optional[Dict]:
        """Scan a single Spring Boot application (not a multi-module project)"""
        print(f"  📂 Scanning single application: {name}...")
        
        service_info = {
            'name': name,
            'path': path,
            'port': 8080,
            'dependencies': set(),
            'has_dockerfile': False,
            'artifact_id': name,
            'is_service': True
        }
        
        # Check for existing Dockerfile
        dockerfile = path / "Dockerfile"
        service_info['has_dockerfile'] = dockerfile.exists()
        
        # Parse pom.xml
        pom_file = path / "pom.xml"
        if pom_file.exists():
            pom_info = self.parse_pom(pom_file)
            service_info.update(pom_info)
            service_info['dependencies'].update(pom_info.get('dependencies', set()))
        
        # Parse application configuration
        config_info = self.parse_config(path)
        if config_info:
            service_info.update(config_info)
        
        # Check if this is a runnable Spring Boot application
        if not self.is_runnable_service(service_info, path):
            print(f"    ℹ️  Not a runnable Spring Boot application")
            return None
            
        # Update global dependencies
        self.dependencies.update(service_info['dependencies'])
        
        print(f"    ✅ Detected dependencies: {', '.join(service_info['dependencies']) if service_info['dependencies'] else 'none'}")
        print(f"    ✅ Port: {service_info['port']}")
        
        return service_info
    
    def is_runnable_service(self, service_info: Dict, path: Path) -> bool:
        """Check if this module is a runnable service"""
        # Check for application config files
        resources_path = path / "src" / "main" / "resources"
        if resources_path.exists():
            config_files = ['application.yml', 'application.yaml', 'application.properties']
            if any((resources_path / f).exists() for f in config_files):
                return True
        
        # Check if Dockerfile exists (manual indication it's a service)
        if service_info['has_dockerfile']:
            return True
        
        # Check pom.xml for spring-boot-maven-plugin
        pom_file = path / "pom.xml"
        if pom_file.exists():
            try:
                content = pom_file.read_text(encoding='utf-8')
                if 'spring-boot-maven-plugin' in content and '<packaging>jar</packaging>' in content:
                    return True
            except Exception:
                pass
        
        return False
    
    def parse_pom(self, pom_path: Path) -> Dict:
        """Parse pom.xml file"""
        info = {
            'artifact_id': pom_path.parent.name,
            'dependencies': set(),
            'packaging': 'jar'  # Default to jar
        }
        
        try:
            content = pom_path.read_text(encoding='utf-8')
            
            # Extract artifactId
            artifact_match = re.search(r'<artifactId>([^<]+)</artifactId>', content)
            if artifact_match:
                info['artifact_id'] = artifact_match.group(1)
            
            # Extract packaging type
            packaging_match = re.search(r'<packaging>([^<]+)</packaging>', content)
            if packaging_match:
                info['packaging'] = packaging_match.group(1).lower()
            
            # Detect dependencies
            for dep_name, patterns in self.DEPENDENCY_PATTERNS.items():
                for pattern in patterns:
                    if re.search(pattern, content, re.IGNORECASE):
                        info['dependencies'].add(dep_name)
                        break
                        
        except Exception as e:
            print(f"    ⚠️  Error parsing POM: {e}")
            
        return info
    
    def parse_config(self, service_path: Path) -> Optional[Dict]:
        """Parse application.yml or application.properties"""
        info = {}
        
        # Look for configuration files
        resources_path = service_path / "src" / "main" / "resources"
        if not resources_path.exists():
            return info
        
        config_files = [
            'application.yml',
            'application.yaml',
            'application.properties',
            'application-docker.yml',
            'application-docker.yaml'
        ]
        
        for config_file in config_files:
            config_path = resources_path / config_file
            if config_path.exists():
                if config_file.endswith(('.yml', '.yaml')):
                    config_info = self.parse_yaml_config(config_path)
                else:
                    config_info = self.parse_properties_config(config_path)
                    
                if config_info:
                    info.update(config_info)
                    
        return info
    
    def parse_yaml_config(self, yaml_path: Path) -> Dict:
        """Parse YAML configuration file and extract ALL relevant configurations"""
        info = {}
        
        try:
            content = yaml_path.read_text(encoding='utf-8')
            data = yaml.safe_load(content)
            
            if not data:
                return info
            
            # Extract server configuration
            if 'server' in data:
                if 'port' in data['server']:
                    info['port'] = int(data['server']['port'])
            
            # Extract spring configuration
            if 'spring' in data:
                spring = data['spring']
                
                # Extract application name
                if 'application' in spring and 'name' in spring['application']:
                    info['app_name'] = spring['application']['name']
                
                # Extract datasource configuration
                if 'datasource' in spring:
                    ds = spring['datasource']
                    
                    # Extract database URL
                    if 'url' in ds:
                        db_url = ds['url']
                        info['datasource_url'] = db_url
                        
                        if 'postgresql' in db_url:
                            info.setdefault('dependencies', set()).add('postgres')
                            # Extract database name from URL (supports various formats)
                            db_name_match = re.search(r'postgresql://[^/]+/([^?]+)', db_url) or \
                                          re.search(r'jdbc:postgresql://[^/]+/([^?]+)', db_url)
                            if db_name_match:
                                info['db_name'] = db_name_match.group(1)
                        elif 'mysql' in db_url:
                            info.setdefault('dependencies', set()).add('mysql')
                            # Extract database name from URL
                            db_name_match = re.search(r'mysql://[^/]+/([^?]+)', db_url) or \
                                          re.search(r'jdbc:mysql://[^/]+/([^?]+)', db_url)
                            if db_name_match:
                                info['db_name'] = db_name_match.group(1)
                    
                    # Extract database username
                    if 'username' in ds:
                        info['db_user'] = ds['username']
                    
                    # Extract database password
                    if 'password' in ds:
                        info['db_password'] = ds['password']
                
                # Extract Kafka configuration
                if 'kafka' in spring:
                    kafka = spring['kafka']
                    info.setdefault('dependencies', set()).add('kafka')  # Mark Kafka as dependency
                    if 'bootstrap-servers' in kafka:
                        info['kafka_bootstrap_servers'] = kafka['bootstrap-servers']
                    if 'consumer' in kafka:
                        if 'group-id' in kafka['consumer']:
                            info['kafka_consumer_group'] = kafka['consumer']['group-id']
                    if 'producer' in kafka:
                        if 'key-serializer' in kafka['producer']:
                            info['kafka_key_serializer'] = kafka['producer']['key-serializer']
                        if 'value-serializer' in kafka['producer']:
                            info['kafka_value_serializer'] = kafka['producer']['value-serializer']
                
                # Extract Redis configuration
                if 'redis' in spring or 'data' in spring and 'redis' in spring['data']:
                    redis = spring.get('redis') or spring.get('data', {}).get('redis', {})
                    info.setdefault('dependencies', set()).add('redis')  # Mark Redis as dependency
                    if 'host' in redis:
                        info['redis_host'] = redis['host']
                    if 'port' in redis:
                        info['redis_port'] = redis['port']
                    if 'password' in redis:
                        info['redis_password'] = redis['password']
                
                # Extract MongoDB configuration
                if 'data' in spring and 'mongodb' in spring['data']:
                    mongo = spring['data']['mongodb']
                    info.setdefault('dependencies', set()).add('mongodb')  # Mark MongoDB as dependency
                    if 'uri' in mongo:
                        info['mongodb_uri'] = mongo['uri']
                    if 'database' in mongo:
                        info['mongodb_database'] = mongo['database']
                
                # Extract RabbitMQ configuration
                if 'rabbitmq' in spring:
                    rabbitmq = spring['rabbitmq']
                    info.setdefault('dependencies', set()).add('rabbitmq')  # Mark RabbitMQ as dependency
                    if 'host' in rabbitmq:
                        info['rabbitmq_host'] = rabbitmq['host']
                    if 'port' in rabbitmq:
                        info['rabbitmq_port'] = rabbitmq['port']
                    if 'username' in rabbitmq:
                        info['rabbitmq_user'] = rabbitmq['username']
                    if 'password' in rabbitmq:
                        info['rabbitmq_password'] = rabbitmq['password']
                
                # Extract Cloud/Eureka configuration
                if 'cloud' in spring:
                    cloud = spring['cloud']
                    if 'config' in cloud and 'uri' in cloud['config']:
                        info['config_server_uri'] = cloud['config']['uri']
                
            # Extract Eureka client configuration
            if 'eureka' in data:
                eureka = data['eureka']
                if 'client' in eureka:
                    client = eureka['client']
                    if 'service-url' in client and 'defaultZone' in client['service-url']:
                        info['eureka_url'] = client['service-url']['defaultZone']
                if 'instance' in eureka:
                    instance = eureka['instance']
                    if 'hostname' in instance:
                        info['eureka_instance_hostname'] = instance['hostname']
                        
        except Exception as e:
            print(f"    ⚠️  Error parsing YAML: {e}")
            
        return info
    
    def parse_properties_config(self, props_path: Path) -> Dict:
        """Parse properties configuration file and extract ALL relevant configurations"""
        info = {}
        
        try:
            content = props_path.read_text(encoding='utf-8')
            
            # Extract server port
            port_match = re.search(r'^\s*server\.port\s*=\s*(\d+)', content, re.MULTILINE)
            if port_match:
                info['port'] = int(port_match.group(1))
            
            # Extract application name
            name_match = re.search(r'^\s*spring\.application\.name\s*=\s*(.+)$', content, re.MULTILINE)
            if name_match:
                info['app_name'] = name_match.group(1).strip()
            
            # Extract datasource configuration
            url_match = re.search(r'spring\.datasource\.url\s*=\s*(.+)$', content, re.MULTILINE)
            if url_match:
                db_url = url_match.group(1).strip()
                info['datasource_url'] = db_url
                
                if 'postgresql' in db_url:
                    info.setdefault('dependencies', set()).add('postgres')
                    # Extract database name from URL (supports various formats)
                    db_name_match = re.search(r'postgresql://[^/]+/([^?\s]+)', db_url) or \
                                  re.search(r'jdbc:postgresql://[^/]+/([^?\s]+)', db_url)
                    if db_name_match:
                        info['db_name'] = db_name_match.group(1)
                elif 'mysql' in db_url:
                    info.setdefault('dependencies', set()).add('mysql')
                    # Extract database name from URL
                    db_name_match = re.search(r'mysql://[^/]+/([^?\s]+)', db_url) or \
                                  re.search(r'jdbc:mysql://[^/]+/([^?\s]+)', db_url)
                    if db_name_match:
                        info['db_name'] = db_name_match.group(1)
            
            # Extract database username
            user_match = re.search(r'^\s*spring\.datasource\.username\s*=\s*(.+)$', content, re.MULTILINE)
            if user_match:
                info['db_user'] = user_match.group(1).strip()
            
            # Extract database password
            pass_match = re.search(r'^\s*spring\.datasource\.password\s*=\s*(.+)$', content, re.MULTILINE)
            if pass_match:
                info['db_password'] = pass_match.group(1).strip()
            
            # Extract Kafka configuration
            kafka_bootstrap = re.search(r'spring\.kafka\.bootstrap-servers\s*=\s*(.+)$', content, re.MULTILINE)
            if kafka_bootstrap:
                info.setdefault('dependencies', set()).add('kafka')  # Mark Kafka as dependency
                info['kafka_bootstrap_servers'] = kafka_bootstrap.group(1).strip()
            
            kafka_group = re.search(r'spring\.kafka\.consumer\.group-id\s*=\s*(.+)$', content, re.MULTILINE)
            if kafka_group:
                info['kafka_consumer_group'] = kafka_group.group(1).strip()
            
            # Extract Redis configuration
            redis_host = re.search(r'spring\.redis\.host\s*=\s*(.+)$', content, re.MULTILINE)
            if redis_host:
                info.setdefault('dependencies', set()).add('redis')  # Mark Redis as dependency
                info['redis_host'] = redis_host.group(1).strip()
            
            redis_port = re.search(r'spring\.redis\.port\s*=\s*(\d+)', content, re.MULTILINE)
            if redis_port:
                info['redis_port'] = int(redis_port.group(1))
            
            redis_pass = re.search(r'spring\.redis\.password\s*=\s*(.+)$', content, re.MULTILINE)
            if redis_pass:
                info['redis_password'] = redis_pass.group(1).strip()
            
            # Extract MongoDB configuration
            mongo_uri = re.search(r'spring\.data\.mongodb\.uri\s*=\s*(.+)$', content, re.MULTILINE)
            if mongo_uri:
                info.setdefault('dependencies', set()).add('mongodb')  # Mark MongoDB as dependency
                info['mongodb_uri'] = mongo_uri.group(1).strip()
            
            mongo_db = re.search(r'spring\.data\.mongodb\.database\s*=\s*(.+)$', content, re.MULTILINE)
            if mongo_db:
                info['mongodb_database'] = mongo_db.group(1).strip()
            
            # Extract RabbitMQ configuration
            rabbit_host = re.search(r'spring\.rabbitmq\.host\s*=\s*(.+)$', content, re.MULTILINE)
            if rabbit_host:
                info.setdefault('dependencies', set()).add('rabbitmq')  # Mark RabbitMQ as dependency
                info['rabbitmq_host'] = rabbit_host.group(1).strip()
            
            rabbit_port = re.search(r'spring\.rabbitmq\.port\s*=\s*(\d+)', content, re.MULTILINE)
            if rabbit_port:
                info['rabbitmq_port'] = int(rabbit_port.group(1))
            
            rabbit_user = re.search(r'spring\.rabbitmq\.username\s*=\s*(.+)$', content, re.MULTILINE)
            if rabbit_user:
                info['rabbitmq_user'] = rabbit_user.group(1).strip()
            
            rabbit_pass = re.search(r'spring\.rabbitmq\.password\s*=\s*(.+)$', content, re.MULTILINE)
            if rabbit_pass:
                info['rabbitmq_password'] = rabbit_pass.group(1).strip()
            
            # Extract Eureka configuration
            eureka_url = re.search(r'eureka\.client\.service-url\.defaultZone\s*=\s*(.+)$', content, re.MULTILINE)
            if eureka_url:
                info['eureka_url'] = eureka_url.group(1).strip()
            
            eureka_hostname = re.search(r'eureka\.instance\.hostname\s*=\s*(.+)$', content, re.MULTILINE)
            if eureka_hostname:
                info['eureka_instance_hostname'] = eureka_hostname.group(1).strip()
            
            # Extract Config Server
            config_uri = re.search(r'spring\.cloud\.config\.uri\s*=\s*(.+)$', content, re.MULTILINE)
            if config_uri:
                info['config_server_uri'] = config_uri.group(1).strip()
                    
        except Exception as e:
            print(f"    ⚠️  Error parsing properties: {e}")
            
        return info


class DockerGenerator:
    """Generates Dockerfiles and docker-compose.yml"""
    
    def __init__(self, scanner: ProjectScanner):
        self.scanner = scanner
        self.compose_services = []
        self.env_vars = []
        self.output_dir = None  # Will be set during generation
        self.eureka_server_name = None  # Dynamically detect Eureka server service name
        
    def detect_eureka_server(self):
        """Detect which service is the Eureka server"""
        for service_name, service_info in self.scanner.services.items():
            if 'eureka-server' in service_info['dependencies']:
                self.eureka_server_name = service_name
                print(f"    ℹ️  Detected Eureka Server: {service_name}")
                return service_name
        return None
        
    def generate_all(self, output_dir: Path = None):
        """Generate all Docker files"""
        if output_dir is None:
            output_dir = self.scanner.root
        
        self.output_dir = output_dir
            
        print("\n🐳 Generating Docker files...")
        
        # Generate Dockerfiles for services without one
        for service_name, service_info in self.scanner.services.items():
            if not service_info['has_dockerfile']:
                self.generate_dockerfile(service_info)
        
        # Generate docker-compose.yml
        self.generate_compose(output_dir)
        
        # Generate .env file
        self.generate_env(output_dir)
        
        print("\n✅ Generation complete!")
        
    def generate_dockerfile(self, service_info: Dict):
        """Generate multi-stage Dockerfile for a service"""
        service_path = service_info['path']
        dockerfile_path = service_path / "Dockerfile"
        
        artifact_id = service_info['artifact_id']
        service_name = service_info['name']
        packaging = service_info.get('packaging', 'jar')  # Default to jar
        port = service_info['port']
        
        # Determine Java version from pom.xml or default to 17
        java_version = 17
        maven_version = "3.9.6"
        pom_file = service_path / "pom.xml"
        if pom_file.exists():
            try:
                content = pom_file.read_text(encoding='utf-8')
                # Look for <java.version>X.X</java.version>
                java_match = re.search(r'<java\.version>(\d+(?:\.\d+)?)</java\.version>', content)
                if java_match:
                    version = java_match.group(1)
                    # Convert 1.8 to 8, keep 11, 17, etc. as is
                    if version == '1.8':
                        java_version = 8
                    else:
                        java_version = int(float(version))
            except Exception:
                pass
        
        # Multi-stage Dockerfile with build inside container
        dockerfile_content = f"""# Stage 1: Build with Maven
FROM maven:{maven_version}-eclipse-temurin-{java_version} AS build
WORKDIR /usr/app
COPY . .
RUN mvn clean package -DskipTests

# Stage 2: Runtime with JRE only
FROM eclipse-temurin:{java_version}-jre
VOLUME /tmp
COPY --from=build /usr/app/target/*.jar {service_name}.jar
ENTRYPOINT ["java","-jar","/{service_name}.jar"]
EXPOSE {port}
"""
        
        print(f"  📝 Creating multi-stage Dockerfile for {service_info['name']} (Java {java_version}, Maven {maven_version})")
        dockerfile_path.write_text(dockerfile_content, encoding='utf-8')
        
    def generate_compose(self, output_dir: Path):
        """Generate docker-compose.yml"""
        print("  📝 Creating docker-compose.yml")
        
        # Detect Eureka server
        self.detect_eureka_server()
        
        compose_content = "services:\n\n"
        networks_needed = False
        volumes_needed = []
        
        # Determine which infrastructure services are needed
        infra_services = self.determine_infrastructure()
        
        # Print infrastructure summary
        if infra_services:
            print(f"    ℹ️  Infrastructure services detected: {', '.join(sorted(infra_services))}")
        
        # Add infrastructure services first
        if 'postgres' in infra_services:
            compose_content += self.generate_postgres_service()
            volumes_needed.append('postgres_data')
            networks_needed = True
        
        if 'mysql' in infra_services:
            compose_content += self.generate_mysql_service()
            volumes_needed.append('mysql_data')
            networks_needed = True
        
        if 'mongodb' in infra_services:
            compose_content += self.generate_mongodb_service()
            volumes_needed.append('mongodb_data')
            networks_needed = True
        
        if 'redis' in infra_services:
            compose_content += self.generate_redis_service()
            volumes_needed.append('redis_data')
            networks_needed = True
        
        if 'kafka' in infra_services:
            compose_content += self.generate_kafka_service()
            volumes_needed.append('kafka_data')
            networks_needed = True
        
        if 'rabbitmq' in infra_services:
            compose_content += self.generate_rabbitmq_service()
            volumes_needed.append('rabbitmq_data')
            networks_needed = True
            
        if 'keycloak' in infra_services:
            compose_content += self.generate_keycloak_service()
            volumes_needed.append('keycloak_data')
            networks_needed = True
        
        # Add application services
        for service_name, service_info in self.scanner.services.items():
            compose_content += self.generate_service_block(service_name, service_info)
            networks_needed = True
        
        # Add volumes section
        if volumes_needed:
            compose_content += "\nvolumes:\n"
            for vol in volumes_needed:
                compose_content += f"  {vol}:\n"
        
        # Add networks section
        if networks_needed:
            compose_content += "\nnetworks:\n"
            compose_content += "  micro-net:\n"
            compose_content += "    driver: bridge\n"
        
        # Write to file
        output_file = output_dir / "docker-compose.generated.yml"
        output_file.write_text(compose_content, encoding='utf-8')
        
    def determine_infrastructure(self) -> Set[str]:
        """Determine which infrastructure services are needed"""
        infra = set()
        
        # Check all dependencies
        all_deps = set()
        for service_info in self.scanner.services.values():
            all_deps.update(service_info['dependencies'])
        
        # Map dependencies to infrastructure
        if 'postgres' in all_deps:
            infra.add('postgres')
        if 'keycloak' in all_deps or 'oauth2-resource-server' in all_deps:
            infra.add('keycloak')
            infra.add('postgres')  # Keycloak needs postgres
        if 'mysql' in all_deps:
            infra.add('mysql')
        if 'mongodb' in all_deps:
            infra.add('mongodb')
        if 'redis' in all_deps:
            infra.add('redis')
        if 'kafka' in all_deps:
            infra.add('kafka')
        if 'rabbitmq' in all_deps:
            infra.add('rabbitmq')
            
        return infra
    
    def collect_db_credentials(self) -> Dict[str, Dict[str, str]]:
        """Collect database credentials from all services"""
        credentials = {
            'mysql': {},
            'postgres': {}
        }
        
        for service_info in self.scanner.services.values():
            # Collect MySQL credentials
            if 'mysql' in service_info['dependencies']:
                if 'db_user' in service_info:
                    credentials['mysql']['user'] = service_info['db_user']
                if 'db_password' in service_info:
                    credentials['mysql']['password'] = service_info['db_password']
                if 'db_name' in service_info:
                    credentials['mysql']['database'] = service_info['db_name']
            
            # Collect PostgreSQL credentials
            if 'postgres' in service_info['dependencies']:
                if 'db_user' in service_info:
                    credentials['postgres']['user'] = service_info['db_user']
                if 'db_password' in service_info:
                    credentials['postgres']['password'] = service_info['db_password']
                if 'db_name' in service_info:
                    credentials['postgres']['database'] = service_info['db_name']
        
        return credentials
    
    def generate_postgres_service(self) -> str:
        """Generate PostgreSQL service block"""
        # Collect actual credentials from services
        db_credentials = self.collect_db_credentials()
        postgres_creds = db_credentials.get('postgres', {})
        
        # Use actual credentials from config files, or defaults if not found
        db_user = postgres_creds.get('user', 'postgres')
        db_pass = postgres_creds.get('password', 'postgres')
        db_name = postgres_creds.get('database', 'postgres')
        
        self.env_vars.extend([
            f"DB_USER={db_user}",
            f"DB_PASS={db_pass}",
            f"DB_NAME={db_name}",
            "DB_PORT=5432"
        ])
        
        # Check for init SQL script
        init_script_paths = [
            self.scanner.root / "docker" / "init-db.sql",
            self.scanner.root / "init-db.sql",
            self.scanner.root / "scripts" / "init-db.sql"
        ]
        
        init_volume = ""
        for script_path in init_script_paths:
            if script_path.exists():
                # Calculate relative path from output_dir
                output_dir = self.output_dir if self.output_dir else self.scanner.root
                try:
                    rel_path = os.path.relpath(script_path, output_dir)
                    # Ensure forward slashes and add ./ prefix for clarity
                    rel_path = './' + rel_path.replace('\\', '/')
                except (ValueError, TypeError):
                    # Fallback to absolute path if different drives
                    rel_path = str(script_path).replace('\\', '/')
                init_volume = f"      - {rel_path}:/docker-entrypoint-initdb.d/init-db.sql\n"
                print(f"    ℹ️  Found init script: {script_path.name}")
                break
        
        return f"""  postgres:
    image: postgres:15
    container_name: postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${{DB_USER:-{db_user}}}
      POSTGRES_PASSWORD: ${{DB_PASS:-{db_pass}}}
      POSTGRES_DB: ${{DB_NAME:-{db_name}}}
    ports:
      - "${{DB_PORT:-5432}}:5432"
    volumes:
{init_volume}      - postgres_data:/var/lib/postgresql/data
    networks:
      - micro-net
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${{DB_USER:-{db_user}}}"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s

"""
    
    def generate_mysql_service(self) -> str:
        """Generate MySQL service block"""
        # Collect actual credentials from services
        db_credentials = self.collect_db_credentials()
        mysql_creds = db_credentials.get('mysql', {})
        
        # Use actual credentials from config files, or defaults if not found
        db_user = mysql_creds.get('user', 'root')
        db_pass = mysql_creds.get('password', 'root')
        db_name = mysql_creds.get('database', 'todoapp')
        
        self.env_vars.extend([
            f"MYSQL_ROOT_PASSWORD={db_pass}",
            f"MYSQL_DATABASE={db_name}",
            f"MYSQL_USER={db_user}",
            f"MYSQL_PASSWORD={db_pass}",
            "MYSQL_PORT=3306"
        ])
        
        # Check for init SQL script
        init_script_paths = [
            self.scanner.root / "docker" / "init-db.sql",
            self.scanner.root / "init-db.sql",
            self.scanner.root / "scripts" / "init-db.sql"
        ]
        
        init_volume = ""
        for script_path in init_script_paths:
            if script_path.exists():
                # Calculate relative path from output_dir
                output_dir = self.output_dir if self.output_dir else self.scanner.root
                try:
                    rel_path = os.path.relpath(script_path, output_dir)
                    # Ensure forward slashes and add ./ prefix for clarity
                    rel_path = './' + rel_path.replace('\\', '/')
                except (ValueError, TypeError):
                    # Fallback to absolute path if different drives
                    rel_path = str(script_path).replace('\\', '/')
                init_volume = f"      - {rel_path}:/docker-entrypoint-initdb.d/init-db.sql\n"
                print(f"    ℹ️  Found init script: {script_path.name}")
                break
        
        return f"""  mysql:
    image: mysql:8
    container_name: mysql
    restart: unless-stopped
    environment:
      MYSQL_ROOT_PASSWORD: ${{MYSQL_ROOT_PASSWORD:-{db_pass}}}
      MYSQL_DATABASE: ${{MYSQL_DATABASE:-{db_name}}}
      MYSQL_USER: ${{MYSQL_USER:-{db_user}}}
      MYSQL_PASSWORD: ${{MYSQL_PASSWORD:-{db_pass}}}
    ports:
      - "${{MYSQL_PORT:-3306}}:3306"
    volumes:
{init_volume}      - mysql_data:/var/lib/mysql
    networks:
      - micro-net
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost", "-u", "${{MYSQL_USER:-{db_user}}}", "-p${{MYSQL_PASSWORD:-{db_pass}}}"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 30s

"""
    
    def generate_mongodb_service(self) -> str:
        """Generate MongoDB service block"""
        # Collect actual MongoDB credentials from services
        mongo_user = 'admin'
        mongo_pass = 'admin'
        mongo_db = 'testdb'
        
        for service_info in self.scanner.services.values():
            if 'mongodb' in service_info['dependencies']:
                if 'mongodb_database' in service_info:
                    mongo_db = service_info['mongodb_database']
                # Extract credentials from URI if present
                if 'mongodb_uri' in service_info:
                    uri = service_info['mongodb_uri']
                    user_match = re.search(r'mongodb://([^:]+):([^@]+)@', uri)
                    if user_match:
                        mongo_user = user_match.group(1)
                        mongo_pass = user_match.group(2)
                break
        
        self.env_vars.extend([
            f"MONGODB_USER={mongo_user}",
            f"MONGODB_PASSWORD={mongo_pass}",
            f"MONGODB_DATABASE={mongo_db}",
            "MONGODB_PORT=27017"
        ])
        
        return f"""  mongodb:
    image: mongo:7
    container_name: mongodb
    restart: unless-stopped
    environment:
      MONGO_INITDB_ROOT_USERNAME: ${{MONGODB_USER:-{mongo_user}}}
      MONGO_INITDB_ROOT_PASSWORD: ${{MONGODB_PASSWORD:-{mongo_pass}}}
      MONGO_INITDB_DATABASE: ${{MONGODB_DATABASE:-{mongo_db}}}
    ports:
      - "${{MONGODB_PORT:-27017}}:27017"
    volumes:
      - mongodb_data:/data/db
    networks:
      - micro-net
    healthcheck:
      test: ["CMD", "mongosh", "--eval", "db.adminCommand('ping')"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s

"""
    
    def generate_redis_service(self) -> str:
        """Generate Redis service block"""
        # Collect actual Redis password from services
        redis_pass = None
        for service_info in self.scanner.services.values():
            if 'redis' in service_info['dependencies']:
                if 'redis_password' in service_info:
                    redis_pass = service_info['redis_password']
                    break
        
        self.env_vars.append("REDIS_PORT=6379")
        if redis_pass:
            self.env_vars.append(f"REDIS_PASSWORD={redis_pass}")
        
        command_line = f"redis-server --requirepass ${{REDIS_PASSWORD:-{redis_pass}}}" if redis_pass else "redis-server"
        
        return f"""  redis:
    image: redis:7-alpine
    container_name: redis
    restart: unless-stopped
    command: {command_line}
    ports:
      - "${{REDIS_PORT:-6379}}:6379"
    volumes:
      - redis_data:/data
    networks:
      - micro-net
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s

"""
    
    def generate_kafka_service(self) -> str:
        """Generate Kafka service block (using KRaft mode, no Zookeeper needed)"""
        self.env_vars.extend([
            "KAFKA_PORT=9092",
            "KAFKA_UI_PORT=8090"
        ])
        
        return """  kafka:
    image: bitnami/kafka:latest
    container_name: kafka
    restart: unless-stopped
    environment:
      KAFKA_CFG_NODE_ID: 1
      KAFKA_CFG_PROCESS_ROLES: broker,controller
      KAFKA_CFG_CONTROLLER_QUORUM_VOTERS: 1@kafka:9093
      KAFKA_CFG_LISTENERS: PLAINTEXT://:9092,CONTROLLER://:9093
      KAFKA_CFG_ADVERTISED_LISTENERS: PLAINTEXT://kafka:9092
      KAFKA_CFG_LISTENER_SECURITY_PROTOCOL_MAP: CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT
      KAFKA_CFG_CONTROLLER_LISTENER_NAMES: CONTROLLER
      KAFKA_CFG_INTER_BROKER_LISTENER_NAME: PLAINTEXT
    ports:
      - "${KAFKA_PORT:-9092}:9092"
    volumes:
      - kafka_data:/bitnami/kafka
    networks:
      - micro-net
    healthcheck:
      test: ["CMD-SHELL", "kafka-broker-api-versions.sh --bootstrap-server localhost:9092 || exit 1"]
      interval: 10s
      timeout: 10s
      retries: 5
      start_period: 30s

  kafka-ui:
    image: provectuslabs/kafka-ui:latest
    container_name: kafka-ui
    restart: unless-stopped
    environment:
      KAFKA_CLUSTERS_0_NAME: local
      KAFKA_CLUSTERS_0_BOOTSTRAPSERVERS: kafka:9092
    ports:
      - "${KAFKA_UI_PORT:-8090}:8080"
    depends_on:
      kafka:
        condition: service_healthy
    networks:
      - micro-net

"""
    
    def generate_rabbitmq_service(self) -> str:
        """Generate RabbitMQ service block"""
        # Collect actual RabbitMQ credentials from services
        rabbit_user = 'guest'
        rabbit_pass = 'guest'
        
        for service_info in self.scanner.services.values():
            if 'rabbitmq' in service_info['dependencies']:
                if 'rabbitmq_user' in service_info:
                    rabbit_user = service_info['rabbitmq_user']
                if 'rabbitmq_password' in service_info:
                    rabbit_pass = service_info['rabbitmq_password']
                break
        
        self.env_vars.extend([
            f"RABBITMQ_USER={rabbit_user}",
            f"RABBITMQ_PASSWORD={rabbit_pass}",
            "RABBITMQ_PORT=5672",
            "RABBITMQ_MANAGEMENT_PORT=15672"
        ])
        
        return f"""  rabbitmq:
    image: rabbitmq:3-management
    container_name: rabbitmq
    restart: unless-stopped
    environment:
      RABBITMQ_DEFAULT_USER: ${{RABBITMQ_USER:-{rabbit_user}}}
      RABBITMQ_DEFAULT_PASS: ${{RABBITMQ_PASSWORD:-{rabbit_pass}}}
    ports:
      - "${{RABBITMQ_PORT:-5672}}:5672"
      - "${{RABBITMQ_MANAGEMENT_PORT:-15672}}:15672"
    volumes:
      - rabbitmq_data:/var/lib/rabbitmq
    networks:
      - micro-net
    healthcheck:
      test: ["CMD", "rabbitmq-diagnostics", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 30s

"""
    
    def generate_keycloak_service(self) -> str:
        """Generate Keycloak service block"""
        self.env_vars.extend([
            "KEYCLOAK_PORT=8180",
            "KEYCLOAK_ADMIN=admin",
            "KEYCLOAK_ADMIN_PASSWORD=admin"
        ])
        
        return """  keycloak:
    image: quay.io/keycloak/keycloak:24.0.4
    container_name: keycloak
    command: start-dev
    restart: unless-stopped
    environment:
      KC_DB: postgres
      KC_DB_URL: jdbc:postgresql://postgres:5432/keycloak_db
      KC_DB_USERNAME: keycloak
      KC_DB_PASSWORD: keycloak
      KEYCLOAK_ADMIN: ${KEYCLOAK_ADMIN:-admin}
      KEYCLOAK_ADMIN_PASSWORD: ${KEYCLOAK_ADMIN_PASSWORD:-admin}
    depends_on:
      postgres:
        condition: service_healthy
        restart: true
    ports:
      - "${KEYCLOAK_PORT:-8180}:8080"
    networks:
      - micro-net
    healthcheck:
      test: ["CMD-SHELL", "exec 3<>/dev/tcp/127.0.0.1/8080"]
      interval: 10s
      timeout: 5s
      retries: 10
      start_period: 90s

"""
    
    def generate_service_block(self, service_name: str, service_info: Dict) -> str:
        """Generate a service block for docker-compose using actual config values"""
        port = service_info['port']
        dependencies = service_info['dependencies']
        
        # Calculate relative path from output_dir to service directory
        service_path = service_info['path']
        if self.output_dir:
            try:
                # Get relative path from output_dir to service directory
                rel_path = os.path.relpath(service_path, self.output_dir)
                # Convert Windows paths to forward slashes for Docker
                context_path = rel_path.replace('\\', '/')
            except (ValueError, TypeError):
                # If relative path fails (e.g., different drives), use absolute path
                context_path = str(service_path).replace('\\', '/')
        else:
            context_path = f"./{service_name}"
        
        # Determine depends_on - use map format for all dependencies to maintain consistency
        depends_on_map = {}  # All dependencies with conditions
        
        # Check for Eureka client dependency
        if 'eureka-client' in dependencies:
            # Use dynamically detected Eureka server name
            if self.eureka_server_name and service_name != self.eureka_server_name:
                depends_on_map[self.eureka_server_name] = 'service_started'
        
        if 'postgres' in dependencies or 'postgres' in self.scanner.dependencies:
            depends_on_map['postgres'] = 'service_healthy'
        
        if 'mysql' in dependencies or 'mysql' in self.scanner.dependencies:
            depends_on_map['mysql'] = 'service_healthy'
            
        if 'keycloak' in dependencies or 'keycloak' in self.scanner.dependencies:
            depends_on_map['keycloak'] = 'service_started'
        
        if 'kafka' in dependencies or 'kafka' in self.scanner.dependencies:
            depends_on_map['kafka'] = 'service_started'
        
        if 'redis' in dependencies or 'redis' in self.scanner.dependencies:
            depends_on_map['redis'] = 'service_started'
        
        if 'rabbitmq' in dependencies or 'rabbitmq' in self.scanner.dependencies:
            depends_on_map['rabbitmq'] = 'service_started'
        
        # Build the service block
        block = f"  {service_name}:\n"
        block += f"    build:\n"
        block += f"      context: {context_path}\n"
        block += f"    container_name: {service_name}\n"
        block += f"    restart: unless-stopped\n"
        block += f"    ports:\n"
        block += f"      - \"${{{service_name.upper().replace('-', '_')}_PORT:-{port}}}:{port}\"\n"
        block += f"    environment:\n"
        block += f"      SPRING_PROFILES_ACTIVE: docker\n"
        
        # Disable OTLP by default (prevents warnings when otel-collector is not available)
        # Remove these lines if using observability stack
        block += f"      MANAGEMENT_OTLP_METRICS_EXPORT_ENABLED: false\n"
        block += f"      MANAGEMENT_OTLP_TRACING_EXPORT_ENABLED: false\n"
        
        # Add database connection environment variables using ACTUAL config values
        if 'mysql' in dependencies or 'mysql' in self.scanner.dependencies:
            # Use actual values from config or fall back to defaults
            db_name = service_info.get('db_name', 'todoapp')
            db_user = service_info.get('db_user', 'root')
            db_password = service_info.get('db_password', 'root')
            
            block += f"      SPRING_DATASOURCE_URL: jdbc:mysql://mysql:3306/${{MYSQL_DATABASE:-{db_name}}}\n"
            block += f"      SPRING_DATASOURCE_USERNAME: ${{MYSQL_USER:-{db_user}}}\n"
            block += f"      SPRING_DATASOURCE_PASSWORD: ${{MYSQL_PASSWORD:-{db_password}}}\n"
        elif 'postgres' in dependencies or 'postgres' in self.scanner.dependencies:
            # Use actual values from config or fall back to defaults
            db_name = service_info.get('db_name', 'postgres')
            db_user = service_info.get('db_user', 'postgres')
            db_password = service_info.get('db_password', 'postgres')
            
            block += f"      SPRING_DATASOURCE_URL: jdbc:postgresql://postgres:5432/${{DB_NAME:-{db_name}}}\n"
            block += f"      SPRING_DATASOURCE_USERNAME: ${{DB_USER:-{db_user}}}\n"
            block += f"      SPRING_DATASOURCE_PASSWORD: ${{DB_PASS:-{db_password}}}\n"
        
        # Add Kafka configuration using actual config values
        if 'kafka' in dependencies or 'kafka' in self.scanner.dependencies:
            kafka_servers = service_info.get('kafka_bootstrap_servers', 'kafka:9092')
            block += f"      SPRING_KAFKA_BOOTSTRAP_SERVERS: ${{KAFKA_BOOTSTRAP_SERVERS:-{kafka_servers}}}\n"
            
            if 'kafka_consumer_group' in service_info:
                block += f"      SPRING_KAFKA_CONSUMER_GROUP_ID: {service_info['kafka_consumer_group']}\n"
        
        # Add Redis configuration using actual config values
        if 'redis' in dependencies or 'redis' in self.scanner.dependencies:
            redis_host = service_info.get('redis_host', 'redis')
            redis_port = service_info.get('redis_port', 6379)
            
            block += f"      SPRING_REDIS_HOST: ${{REDIS_HOST:-{redis_host}}}\n"
            block += f"      SPRING_REDIS_PORT: ${{REDIS_PORT:-{redis_port}}}\n"
            
            if 'redis_password' in service_info:
                block += f"      SPRING_REDIS_PASSWORD: ${{REDIS_PASSWORD:-{service_info['redis_password']}}}\n"
        
        # Add RabbitMQ configuration using actual config values
        if 'rabbitmq' in dependencies or 'rabbitmq' in self.scanner.dependencies:
            rabbit_host = service_info.get('rabbitmq_host', 'rabbitmq')
            rabbit_port = service_info.get('rabbitmq_port', 5672)
            rabbit_user = service_info.get('rabbitmq_user', 'guest')
            rabbit_pass = service_info.get('rabbitmq_password', 'guest')
            
            block += f"      SPRING_RABBITMQ_HOST: ${{RABBITMQ_HOST:-{rabbit_host}}}\n"
            block += f"      SPRING_RABBITMQ_PORT: ${{RABBITMQ_PORT:-{rabbit_port}}}\n"
            block += f"      SPRING_RABBITMQ_USERNAME: ${{RABBITMQ_USER:-{rabbit_user}}}\n"
            block += f"      SPRING_RABBITMQ_PASSWORD: ${{RABBITMQ_PASSWORD:-{rabbit_pass}}}\n"
        
        # Add Eureka configuration using actual config values
        if 'eureka_url' in service_info:
            # Replace localhost/127.0.0.1 with actual Eureka server container name
            eureka_url = service_info['eureka_url']
            if self.eureka_server_name:
                eureka_url = eureka_url.replace('localhost', self.eureka_server_name).replace('127.0.0.1', self.eureka_server_name)
            block += f"      EUREKA_CLIENT_SERVICE_URL_DEFAULTZONE: ${{EUREKA_URL:-{eureka_url}}}\n"
        elif 'eureka-client' in dependencies and self.eureka_server_name:
            # Default Eureka URL using detected Eureka server name
            eureka_port = self.scanner.services.get(self.eureka_server_name, {}).get('port', 8761)
            block += f"      EUREKA_CLIENT_SERVICE_URL_DEFAULTZONE: ${{EUREKA_URL:-http://{self.eureka_server_name}:{eureka_port}/eureka/}}\n"
        
        # Add MongoDB configuration using actual config values
        if 'mongodb' in dependencies or 'mongodb' in self.scanner.dependencies:
            if 'mongodb_uri' in service_info:
                mongo_uri = service_info['mongodb_uri'].replace('localhost', 'mongodb').replace('127.0.0.1', 'mongodb')
                block += f"      SPRING_DATA_MONGODB_URI: ${{MONGODB_URI:-{mongo_uri}}}\n"
            if 'mongodb_database' in service_info:
                block += f"      SPRING_DATA_MONGODB_DATABASE: {service_info['mongodb_database']}\n"
        
        # Add Config Server configuration using actual config values
        if 'config_server_uri' in service_info:
            config_uri = service_info['config_server_uri'].replace('localhost', 'config-server').replace('127.0.0.1', 'config-server')
            block += f"      SPRING_CLOUD_CONFIG_URI: ${{CONFIG_SERVER_URI:-{config_uri}}}\n"
        
        # Add depends_on if needed - always use map format with conditions
        if depends_on_map:
            block += f"    depends_on:\n"
            for dep_name, condition in depends_on_map.items():
                block += f"      {dep_name}:\n"
                block += f"        condition: {condition}\n"
                # Add restart flag for dependencies
                if dep_name in ['postgres', 'mysql', 'keycloak']:
                    block += f"        restart: true\n"
        
        # Add health check for Spring Boot services
        # Try to use actuator health endpoint, but make it non-critical
        block += f"    healthcheck:\n"
        block += f"      test: [\"CMD-SHELL\", \"curl -f http://localhost:{port}/actuator/health 2>/dev/null || curl -f http://localhost:{port}/health 2>/dev/null || exit 1\"]\n"
        block += f"      interval: 30s\n"
        block += f"      timeout: 10s\n"
        block += f"      retries: 5\n"
        block += f"      start_period: 60s\n"
        
        block += f"    networks:\n"
        block += f"      - micro-net\n"
        block += "\n"
        
        # Add env var for port
        self.env_vars.append(f"{service_name.upper().replace('-', '_')}_PORT={port}")
        
        return block
    
    def generate_env(self, output_dir: Path):
        """Generate .env file"""
        print("  📝 Creating .env file")
        
        env_content = "# Auto-generated environment variables\n"
        env_content += "# Modify as needed\n\n"
        
        # Remove duplicates while preserving order
        seen = set()
        unique_vars = []
        for var in self.env_vars:
            key = var.split('=')[0]
            if key not in seen:
                seen.add(key)
                unique_vars.append(var)
        
        env_content += "\n".join(unique_vars)
        env_content += "\n"
        
        output_file = output_dir / ".env.generated"
        output_file.write_text(env_content, encoding='utf-8')


def main():
    """Main function"""
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description='Automatic Docker Generator for Microservices',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  python auto-docker-generator.py /path/to/project
  python auto-docker-generator.py ./micros-main
  python auto-docker-generator.py C:\\Projects\\my-microservices
        '''
    )
    parser.add_argument(
        'project_path',
        nargs='?',
        default=None,
        help='Path to the project root (can be multi-module with parent pom.xml or individual services)'
    )
    parser.add_argument(
        '-o', '--output',
        default=None,
        help='Output directory for generated files (default: project root)'
    )
    parser.add_argument(
        '--skip-version-check',
        action='store_true',
        help='Skip version consistency checking'
    )
    parser.add_argument(
        '--align-versions',
        action='store_true',
        help='Automatically align versions without prompting'
    )
    parser.add_argument(
        '--check-versions-only',
        action='store_true',
        help='Only check versions without generating Docker files'
    )
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("🚀 Automatic Docker Generator for Microservices")
    print("=" * 60)
    
    # Determine project root
    if args.project_path:
        project_root = _normalize_path(args.project_path)
        if not project_root.exists():
            print(f"❌ Error: Path does not exist: {project_root}")
            sys.exit(1)
        if not project_root.is_dir():
            print(f"❌ Error: Path is not a directory: {project_root}")
            sys.exit(1)
        print(f"📁 Using project root: {project_root}")
    else:
        # Auto-detect project root
        cwd = Path.cwd()
        if (cwd / "micros-main").exists():
            project_root = cwd / "micros-main"
            print(f"📁 Auto-detected project root: {project_root}")
        elif (cwd / "pom.xml").exists():
            project_root = cwd
            print(f"📁 Auto-detected project root: {project_root}")
        else:
            # Use current directory
            project_root = cwd
            print(f"📁 Using current directory as project root: {project_root}")
    
    # Determine output directory
    if args.output:
        output_dir = Path(args.output).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        print(f"📂 Output directory: {output_dir}")
    else:
        output_dir = project_root
    
    # Scan project
    scanner = ProjectScanner(str(project_root))
    if not scanner.scan():
        print("❌ Failed to scan project")
        sys.exit(1)
    
    # Print summary
    print(f"\n📊 Summary:")
    print(f"   Services found: {len(scanner.services)}")
    for name, info in scanner.services.items():
        print(f"     - {name}: port {info['port']}, deps: {', '.join(sorted(info['dependencies'])) or 'none'}")
    
    # Check version consistency (unless skipped)
    if not args.skip_version_check and scanner.services:
        print(f"\n🔍 Checking version consistency...")
        is_consistent, version_groups = VersionChecker.check_consistency(scanner.services)
        
        if is_consistent:
            print(f"✅ All services use consistent versions!")
            # Show what versions are being used
            for service_name in list(scanner.services.keys())[:1]:
                pom_file = scanner.services[service_name]['path'] / 'pom.xml'
                if pom_file.exists():
                    versions = VersionChecker.extract_versions(pom_file)
                    print(f"   Spring Boot: {versions['spring_boot']}")
                    print(f"   Spring Cloud: {versions['spring_cloud']}")
                    print(f"   Java: {versions['java']}")
        else:
            print(f"⚠️  Version inconsistencies detected!")
            print(f"\n📋 Current versions across services:")
            
            # Show Spring Boot versions
            print(f"\n  Spring Boot:")
            for version, services in sorted(version_groups['spring_boot'].items()):
                if version != 'N/A':
                    print(f"    {version}: {', '.join(services)}")
            
            # Show Spring Cloud versions
            print(f"\n  Spring Cloud:")
            for version, services in sorted(version_groups['spring_cloud'].items()):
                print(f"    {version}: {', '.join(services)}")
            
            # Show Java versions
            print(f"\n  Java:")
            for version, services in sorted(version_groups['java'].items()):
                if version != 'N/A':
                    print(f"    {version}: {', '.join(services)}")
            
            # Determine target versions
            target_versions = VersionChecker.determine_target_versions(version_groups)
            print(f"\n💡 Recommended target versions:")
            print(f"   Spring Boot: {target_versions.get('spring_boot', 'N/A')}")
            print(f"   Spring Cloud: {target_versions.get('spring_cloud', 'N/A')}")
            print(f"   Java: {target_versions.get('java', 'N/A')}")
            
            # Auto-align or prompt user
            should_align = args.align_versions
            if not should_align and not args.check_versions_only:
                response = input(f"\n❓ Align all services to recommended versions? (y/n): ").strip().lower()
                should_align = response == 'y'
            
            if should_align:
                print(f"\n🔧 Aligning versions...")
                aligned_count = VersionChecker.align_versions(scanner.services, target_versions)
                print(f"✅ Aligned {aligned_count} service(s)")
            elif not args.check_versions_only:
                print(f"\n⚠️  Continuing with inconsistent versions. This may cause runtime issues!")
        
        # If check-only mode, exit here
        if args.check_versions_only:
            print(f"\n✅ Version check complete")
            sys.exit(0)
    
    # Generate Docker files
    generator = DockerGenerator(scanner)
    generator.generate_all(output_dir)
    
    print(f"\n📂 Files generated in: {output_dir}")
    print(f"   - docker-compose.generated.yml")
    print(f"   - .env.generated")
    print(f"   - Dockerfiles (in each service directory where missing)")
    
    print(f"\n💡 Next steps:")
    print(f"   1. Review and customize .env.generated if needed")
    print(f"   2. Build and start all services (builds happen inside Docker!):")
    if output_dir != project_root:
        print(f"      docker compose -f {output_dir}/docker-compose.generated.yml up -d --build")
    else:
        print(f"      cd {output_dir}")
        print(f"      docker compose -f docker-compose.generated.yml up -d --build")
    print(f"   3. View logs: docker compose -f docker-compose.generated.yml logs -f")
    print(f"   4. Check status: docker compose -f docker-compose.generated.yml ps")
    
    # Print infrastructure services summary
    infra = generator.determine_infrastructure()
    if infra:
        print(f"\n📦 Infrastructure services included:")
        infra_info = {
            'postgres': 'PostgreSQL database (port 5432)',
            'mysql': 'MySQL database (port 3306)',
            'mongodb': 'MongoDB NoSQL database (port 27017)',
            'redis': 'Redis cache (port 6379)',
            'kafka': 'Apache Kafka message broker (port 9092) + Kafka UI (port 8090)',
            'rabbitmq': 'RabbitMQ message broker (port 5672) + Management UI (port 15672)',
            'keycloak': 'Keycloak authentication server (port 8180)'
        }
        for service in sorted(infra):
            if service in infra_info:
                print(f"   - {infra_info[service]}")
    
    print(f"\n✅ Benefits of multi-stage builds:")
    print(f"   - No local Maven or Java installation required")
    print(f"   - Consistent builds across all environments")
    print(f"   - Smaller final images (only JRE, not full JDK)")
    print(f"\n⚠️  Important:")
    print(f"   - First build will take 5-10 minutes (downloads dependencies)")
    print(f"   - Subsequent builds are much faster (Docker cache)")
    print(f"   - Wait ~2 minutes after build for all services to be fully ready")
    print(f"\n🌐 Access URLs:")
    for service_name, info in scanner.services.items():
        port = info['port']
        print(f"   - {service_name}: http://localhost:{port}")
    if 'kafka' in infra:
        print(f"   - Kafka UI: http://localhost:8090")
    if 'rabbitmq' in infra:
        print(f"   - RabbitMQ Management: http://localhost:15672")
    if 'keycloak' in infra:
        print(f"   - Keycloak Admin: http://localhost:8180")
    

if __name__ == "__main__":
    main()
