import os
import json
from datetime import datetime, timedelta, timezone
from git import Repo, GitCommandError
import yaml
import requests
import argparse

class NucleiTemplate:
    def __init__(self, filepath, creation_time, category, name, severity, description, raw_url):
        self.filepath = filepath
        self.creation_time = creation_time
        self.category = category
        self.name = name
        self.severity = severity
        self.description = description
        self.raw_url = raw_url

    def to_dict(self):
        """Serializes the object to a dictionary, suitable for JSON."""
        return {
            "creation_time": self.creation_time.isoformat() if self.creation_time else None,
            "filepath": self.filepath,
            "raw_url": self.raw_url,
            "name": self.name,
            "category": self.category,
            "severity": self.severity,
            "description": self.description
        }

    @staticmethod
    def from_dict(data):
        """Deserializes the object from a dictionary."""
        data['creation_time'] = datetime.fromisoformat(data['creation_time']) if data.get('creation_time') else None
        return NucleiTemplate(**data)

class NucleiTemplateManager():
    def __init__(self, repo_url, repo_local_path, templates_file_path=None):
        self.repo_url = repo_url
        self.repo_local_path = repo_local_path
        self.templates = {}
        self.repo = None
        self.templates_file_path = templates_file_path

    def get_templates(self):
        return self.templates

    def load_templates_from_db(self, db_path):
        try:
            with open(db_path, 'r') as json_file:
                templates_data = json.load(json_file)
                self.templates = {data['name']: NucleiTemplate.from_dict(data) for data in templates_data}
        except Exception as err:
            print (err)
            return {}
    
    def save_templates(self, db_file_path):
        templates_data = [template.to_dict() for template in self.templates.values()]

        if not templates_data:
            print ('Nothing to save')
            return 
        
        with open(db_file_path, 'w') as json_file:
            json.dump(templates_data, json_file, default=str, indent=4)
    
    def update_repository_local(self):
        if not os.path.exists(self.repo_local_path):
            try:
                return Repo.clone_from(self.repo_url, self.repo_local_path)
            except GitCommandError as error:
                print(f"Failed to clone repository: {error}")
                return None
        repo = Repo(self.repo_local_path)
        repo.git.pull()
        self.repo = repo

    def find_template_creation_date(self, file_path):
        # Get the list of commits that include the specified file, ordered from latest to earliest
        commits = list(self.repo.iter_commits(paths=file_path))
        
        # The last commit in the list, if any, should be the first commit where the file appears
        if commits:
            first_commit = commits[-1]
            return first_commit.committed_datetime
        else:
            return None
    
    def get_commits(self, hours_ago):
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(hours=hours_ago)
        return list(self.repo.iter_commits(since=start_date.isoformat(), until=end_date.isoformat()))

    def update_templates_from_commits(self, commits):
        for commit in commits:
            for filepath in list(commit.stats.files):
                if filepath.endswith(".yaml"):

                    name = os.path.basename(filepath).split('.')[0]  # Use file name as unique identifier
                    if name in self.templates:
                        continue  
                        
                    try:
                        local_path = os.path.join(self.repo.working_dir, filepath)
                        category = filepath.split('/')[0]
                        with open(local_path, 'r') as file:
                            data = yaml.safe_load(file)
                            template_severity = data.get('info', {}).get('severity', 'Unknown')
                            template_description = data.get('info', {}).get('description', 'No description available.')
                        
                        creation_time = self.find_template_creation_date(filepath)

                        template = NucleiTemplate(
                            name = name,
                            filepath=filepath,
                            creation_time=creation_time,
                            category=category,
                            severity=template_severity,
                            description=template_description,
                            raw_url=f'https://raw.githubusercontent.com/projectdiscovery/nuclei-templates/main/{filepath}'
                            )
                                                
                    except Exception as err:
                        continue

                    self.templates[name] = template
    
    def load_data_for_last_hours(self, hours_ago):
        self.update_repository_local()
        if self.templates_file_path:
            self.load_templates_from_db(self.templates_file_path)
        
        new_commits = self.get_commits(hours_ago)
        self.update_templates_from_commits(new_commits)


def filter_templates(template, category, severity, hours):
    current_time = datetime.now(timezone.utc)
    category_condition = (template.category in category) if category else True
    severity_condition = (template.severity in severity) if severity else True
    time_condition = current_time - template.creation_time < timedelta(hours=hours)
    return category_condition and severity_condition and time_condition


def main():
    parser = argparse.ArgumentParser(description='Filter templates and extract raw URLs.')
    parser.add_argument('--hours', type=int, default=8, help='Hours ago for creation time filter')
    parser.add_argument('--category', type=str, help='Comma separated categories to filter by. Omit for all.')
    parser.add_argument('--severity', type=str, help='Comma separated severities levels to include. Omit for all.')
    parser.add_argument('--output', type=str, help='File to save results')

    args = parser.parse_args()

    current_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(current_dir, 'settings.yml')
    with open(config_path, 'r') as file:
        settings = yaml.safe_load(file)

    # settings = {
    #     'repository': {
    #         'url': 'https://github.com/projectdiscovery/nuclei-templates.git',
    #         'local_path': '/tmp/nuclei-templates'
    #     },
    #     'cache_file': 'templates_cache.json'
    # }

    nuclei_manager = NucleiTemplateManager(
        repo_url=settings['repository']['url'],
        repo_local_path=settings['repository']['local_path'],
        templates_file_path = settings['cache_file']
    )

    hours = args.hours
    if args.severity:
        severity = [severity.lower().strip() for severity in args.severity.split(",") if args.severity]
    else:
        severity = []

    if args.category:
        category = [category.lower().strip() for category in args.category.split(",") if args.category]
    else:
        category = []

    nuclei_manager.load_data_for_last_hours(hours)
    nuclei_manager.save_templates(settings['cache_file'])

    all_templates = nuclei_manager.get_templates().values()
    filtered_templates = [template for template in all_templates if filter_templates(template, category, severity, hours)]
    filtered_templates_json = [obj.to_dict() for obj in filtered_templates]
    
    if args.output:
        with open(args.output, 'w') as file:
            for template_json in filtered_templates_json:
                # Convert the dictionary to a JSON string and write it to the file
                json_str = json.dumps(template_json)
                file.write(json_str + '\n')

if __name__ == "__main__":
    main()
