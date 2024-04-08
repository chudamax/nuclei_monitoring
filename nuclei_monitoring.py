import requests
import os
import json
from datetime import datetime, timedelta, timezone

from git import Repo, GitCommandError
import yaml

class NucleiTemplate:
    def __init__(self, filepath, raw_url=None, commit_date=None, status=None):
        self.commit_date = commit_date
        self.filepath = filepath
        self.raw_url = raw_url
        self.name = None
        self.category = self.extract_category_from_url(raw_url)
        self.severity = None
        self.description = None
        self.status = status
        self.load_attributes()
    
    @property
    def unique_id(self):
        return self.name  # or self.filepath if more appropriate

    def load_attributes(self):
        """Loads template attributes from the YAML file."""
        with open(self.filepath, 'r') as file:
            data = yaml.safe_load(file)
            self.name = data.get('id', 'Unknown')
            self.severity = data.get('info', {}).get('severity', 'Unknown')
            self.description = data.get('info', {}).get('description', 'No description available.')

    def extract_category_from_url(self, url):
        """Extracts the template category from the raw URL, defined as the first word after 'main'."""
        if url:
            parts = url.split('/')
            if "main" in parts:
                # The category is the segment immediately after 'main'
                main_index = parts.index("main")
                # Check if there's at least one segment following 'main'
                if main_index + 1 < len(parts):
                    return parts[main_index + 1]
        return "Uncategorized"
    
    def to_json(self):
        """Serializes the object to a JSON string."""
        # Convert datetime to string for JSON serialization
        obj_dict = self.__dict__.copy()
        obj_dict['commit_date'] = obj_dict['commit_date'].isoformat() if obj_dict['commit_date'] else None
        return json.dumps(obj_dict, indent=4)
    
    @staticmethod
    def from_json(json_str):
        """Deserializes the object from a JSON string."""
        obj_dict = json.loads(json_str)
        # Convert the datetime string back to a datetime object
        obj_dict['commit_date'] = datetime.fromisoformat(obj_dict['commit_date']) if obj_dict['commit_date'] else None
        return NucleiTemplate(**obj_dict)
    
    def __repr__(self):
        return f"NucleiTemplate(name={self.name}, severity={self.severity}, description={self.description[:30]}, raw_url={self.raw_url})"
    

def send_telegram_message(telegram_chat_id, message):
    """Sends message to a Telegram chat."""
    url = f"https://api.telegram.org/bot{telegram_bot_token}/sendMessage"
    data = {
        "chat_id": telegram_chat_id,
        "text": message,
        "parse_mode": "HTML"
    }
    response = requests.post(url, data=data)
    response.raise_for_status()

def clone_or_update_repository(repo_url, repo_path):
    """Clones the Nuclei templates repo if it doesn't exist, or pulls updates if it does."""
    if not os.path.exists(repo_path):
        try:
            Repo.clone_from(repo_url, repo_path)
        except GitCommandError as error:
            print(f"Failed to clone repository: {error}")
            return None
    repo = Repo(repo_path)
    origin = repo.remotes.origin
    origin.pull()
    return repo

def get_commits_by_date(repo, hours_ago):
    """Fetches commits from the last specified number of days."""
    end_date = datetime.now()
    start_date = end_date - timedelta(hours=hours_ago)
    commits = list(repo.iter_commits(since=start_date.isoformat(), until=end_date.isoformat()))
    return commits

def is_template_new(repo, file_path):
    print(file_path)
    """
    Determines if the template is newly created based on its commit history.
    Assumes files are not renamed.
    """
    commits_for_file = list(repo.iter_commits(paths=file_path))
    # If the file has only one commit, it's considered new. Otherwise, it's considered modified.
    return len(commits_for_file) == 1


def find_templates_in_commits(repo, commits):
    """Finds new templates in the specified commits and creates NucleiTemplate objects, avoiding duplicates."""
    new_templates = {}

    for commit in commits:
        commit_date = commit.committed_datetime.astimezone(timezone.utc)
        try:
            for file_path in commit.stats.files:
                if file_path.endswith(".yaml"):
                    raw_url = f"https://raw.githubusercontent.com/projectdiscovery/nuclei-templates/main/{file_path}"
                    
                    template = NucleiTemplate(filepath=os.path.join(repo.working_dir, file_path),
                                              raw_url=raw_url,
                                              commit_date=commit_date
                                              )  
                    
                    # Logic to ensure the most recent commit for a file is used
                    if template.name not in new_templates or commit_date > new_templates[template.name].commit_date:
                        template.status = "new" if is_template_new(repo, file_path) else "modified"
                        new_templates[template.name] = template
        except Exception as ex:
            print(f"Error processing file '{file_path}': {ex}")
    
    return list(new_templates.values())

def save_templates_to_json(templates, json_file_path):
    with open(json_file_path, 'w') as json_file:
        # Serialize each template to JSON and write it to the file
        json_file.writelines([template.to_json() + "\n" for template in templates])


def load_templates_from_json(json_file_path):
    templates = []
    with open(json_file_path, 'r') as json_file:
        for line in json_file:
            templates.append(NucleiTemplate.from_json(line.strip()))
    return templates

def load_templates_cache(json_file_path):
    try:
        with open(json_file_path, 'r') as json_file:
            templates_data = json.load(json_file)
        # Convert list to a dictionary for faster lookup, using the unique identifier
        return {template_data['name']: template_data for template_data in templates_data}
    except FileNotFoundError:
        return {}
    
def main():

    # Load settings from settings.yml
    with open('settings.yml', 'r') as file:
        settings = yaml.safe_load(file)

    json_file_path = 'templates_cache.json'
    github_token = settings['github']['token']
    repo_url = settings['repository']['url']
    repo_path = settings['repository']['local_path']
    telegram_bot_token = settings['telegram']['bot_token']
    telegram_chat_id = settings['telegram']['chat_id']

    # Check if the cache file exists and is not empty
    if os.path.exists(json_file_path) and os.path.getsize(json_file_path) > 0:
        # Load templates from the cache
        templates = load_templates_from_json(json_file_path)
        print("Loaded templates from cache.")
    else:
        # Fetch new data if cache is not available
        repo = clone_or_update_repository(repo_url, repo_path)
        if repo:
            all_commits = get_commits_by_date(repo, 4)  # Adjust time frame as needed
            templates = find_templates_in_commits(repo, all_commits)
            # Save the fetched templates to cache for future runs
            save_templates_to_json(templates, json_file_path)
            print("Fetched new templates and updated cache.")
    
    # Example: Processing loaded or fetched templates
    for template in templates:
        print(template.name, template.status)


        # Filter templates by date
        #templates_last_1_day = filter_templates_by_date(all_templates, 1)
        #templates_last_1_day_http = filter_templates_by_category(templates_last_1_day, 'http')

        #templates_last_7_days = filter_templates_by_date(all_templates, 7)
        #templates_last_7_days_http = filter_templates_by_category(templates_last_7_days, 'http')
        #templates_last_30_days = all_templates  # Already filtered to last 30 days

        # Write raw URLs to files
        #write_raw_urls_to_file(templates_last_1_day_http, 'new_templates_1_day_http.txt')
        #write_raw_urls_to_file(templates_last_7_days_http, 'new_templates_7_days_http.txt')
        #write_raw_urls_to_file(templates_last_30_days, 'new_templates_30_days.txt')


    # Handle new pull requests
    # last_check_time = get_last_check_pr_time()
    # new_prs = fetch_new_prs(last_check_time)
    # if new_prs:
    #     pr_message = f"New Pull Requests in Nuclei Templates:\n {new_prs}"
    #     print (pr_message)
    #     send_telegram_message(pr_message)
    #     #save_list_to_file(new_pull_requests_file, [f"{pr['title']} - {pr['html_url']}" for pr in new_prs])
    #     save_new_prs(new_prs)
    #     save_last_check_pr_time()

if __name__ == "__main__":
    main()



# def get_last_check_pr_time():
#     """Gets the last check time for pull requests."""
#     if os.path.exists(last_check_pr_file):
#         with open(last_check_pr_file, 'r') as file:
#             last_check_time_str = file.read().strip()

#             if last_check_time_str == '':
#                 return None
            
#             last_check_time = datetime.fromisoformat(file.read().strip())
#             return last_check_time
#     return None

# def save_last_check_pr_time():
#     """Saves the current check time for pull requests."""
#     with open(last_check_pr_file, 'w') as file:
#         file.write(datetime.now().isoformat())

# def fetch_new_prs(last_check_time):
#     """Fetches new pull requests since the last check time and determines if they add or modify files."""
#     headers = {'Authorization': f'token {github_token}'}
#     pr_url = 'https://api.github.com/repos/projectdiscovery/nuclei-templates/pulls'
#     params = {'state': 'open', 'sort': 'created', 'direction': 'desc'}
#     response = requests.get(pr_url, headers=headers, params=params)
#     prs = response.json()
#     new_prs_info = []

#     for pr in prs:
#         created_at = datetime.strptime(pr['created_at'], '%Y-%m-%dT%H:%M:%SZ')
#         if last_check_time and created_at <= last_check_time:
#             continue

#         # Fetch files for the PR
#         files_response = requests.get(pr['url'] + '/files', headers=headers, params={'per_page': 100})
#         files_response.raise_for_status()
#         files = files_response.json()

#         # Determine if there are new files
#         new_files = [f for f in files if f['status'] == 'added']
#         pr_info = {
#             'id': pr['id'],
#             'title': pr['title'],
#             'created_at': pr['created_at'],
#             'html_url': pr['html_url'],
#             'new_files': len(new_files),
#             'total_files': len(files)
#         }
#         new_prs_info.append(pr_info)

#     return new_prs_info

# def save_prs(prs):
#     with open(new_pull_requests_file, 'w') as file:
#         for pr in prs:
#             pr_details = f"PR ID: {pr['id']}, Title: {pr['title']}, Created At: {pr['created_at']}, URL: {pr['html_url']}, New Files: {pr['new_files']}, Total Files: {pr['total_files']}\n"
#             file.write(pr_details)

# def filter_templates_by_date(templates, days):
#     """Filters templates added within the specified number of days, ensuring timezone-aware comparison."""
#     # Ensure the cutoff_date is offset-aware by using timezone.utc
#     cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
#     return [template for template in templates if template.commit_date >= cutoff_date]

# def filter_templates_by_category(templates, category):
#     """Filters templates by a given category."""
#     return [template for template in templates if template.category.lower() == category.lower()]

# def write_raw_urls_to_file(templates, filename):
#     """Writes the raw URLs of the given templates to a file."""
#     with open(filename, 'w') as file:
#         for template in templates:
#             file.write(template.raw_url + '\n')