
import sys
import os
import xml.etree.cElementTree as ET
import subprocess
import re
from contextlib import chdir
from shutil import copytree

# import openai

# TODO: Would probably be nice to use Pathlib for all paths instead of plain strings

GENERATION_SUCCESS_PATTERN = re.compile(r".*Project generation completed.*")
BUILD_ERROR_PATTERN = re.compile(r".*error:.*")
BUILD_WARNING_PATTERN = re.compile(r".*warning:.*")
litellm_client = None

def parse_xml(xml_file: str, predefined_examples: list[str], predefined_boards: list[str]) -> dict[str,list[str]]:
    """ Collect the sample app paths and supported boards, and return as dict. """
    tree = ET.parse(xml_file)
    root = tree.getroot()
    sample_apps = {}
    print(predefined_examples, predefined_boards)
    for sample_app in root.findall("descriptors"):
        if predefined_examples and (sample_app.get("name") not in predefined_examples): continue
        for app_properties in sample_app.findall("properties"):
            if app_properties.get("key") == "boardCompatibility":
                compatible_boards = app_properties.get("value").split(' ')
                
                # The list contains some extra spaces and strings that are not used. Remove all strings without numbers
                compatible_boards = [board for board in compatible_boards if any(chr.isdigit() for chr in board)] 
                if predefined_boards: compatible_boards = predefined_boards
            elif app_properties.get("key") == "projectFilePaths":
                sample_app_path = app_properties.get("value")
                
        sample_apps.update({sample_app_path: compatible_boards})
    
    return sample_apps

def generate_project(slcp_path:str, app_board_specific_path:str, board:str):
    """ Generate the project using SLC-CLI. """
    project_name = app_board_specific_path.split('/')[-1]
    print(f"Generating {project_name}...")
    try:
        subprocess.run(["slc", "generate", "-p", slcp_path, "--new-project", "-d" , app_board_specific_path, 
                        f"-name={project_name}", "--output-type", "cmake", "--with", board],
                        capture_output=True,
                        text=True,
                        check=True
                        )     
    except subprocess.CalledProcessError:
        raise

def handle_generation_error(e: subprocess.CalledProcessError, project_path: str, failed_generations: list) -> None:
    os.makedirs(project_path)
    with open(f"{project_path}/generation_error.log", "w") as f:
        f.write(e.stdout)
    project_name = project_path.split("/")[-1]
    failed_generations.append(project_name)
    # instruction = f"Do not incorporate the SLF4J logger warning. Incorporate the project name: {project_name}. Analyse the following project generation log and determine the reason of its failure in two to three sentences: "
    # ask_ai(instruction, e.stderr, "litellm_failed_generations")

def handle_build_error(e: subprocess.CalledProcessError, project_name: str) -> None:
    os.makedirs(project_name)
    with open(f"{project_name}/build_error.log", "w") as f:
        f.write(e.stderr)

def compile_project(cmake_path:str):
    """ Compile the project using make. """
    project_name = cmake_path.split('/')[-1].strip('_cmake')
    print(f"Building {project_name}...")
    with(chdir(cmake_path)):
        try:
            subprocess.run(["cmake", "--workflow", "--preset", "project"],
                        capture_output=True,
                        text=True,
                        check=True
                        )            
        except subprocess.CalledProcessError:
            raise
def check_final_results(failed_generations: list, failed_builds: list, warning_builds: list) -> bool:
    " Print the failed projects. "
    success = True        
    if len(failed_generations) > 0:
        success = False
        print(f"Failed to generate {len(failed_generations)} projects:")
        for failed_project in failed_generations:
            print(failed_project)
        print("\n")
    if len(failed_builds) > 0:
        success = False
        print(f"Failed to build {len(failed_builds)} projects:")
        for failed_build in failed_builds:
            print(failed_build)
        print("\n")
    if len(warning_builds) > 0:
        print(f"Warning during the build of {len(warning_builds)} projects:")
        for warning_build in warning_builds:
            print(warning_build)
        print("\n")
    
    litellm_failed_generations = open("tmp/litellm_failed_generations")
    litellm_failed_builds = open("tmp/litellm_failed_builds")
    instruction = "Format it nicely for a Slack message, keep it short and plain, return only with the message. A unique project name consist the project identifier and the board, such as project_id_board. Summarize and group the following Jenkins pipeline errors by project_id, only mention the project_id once per error, only list maximum three of the boards per error: "
    ask_ai(instruction, f"{litellm_failed_generations.read()} {litellm_failed_builds.read()}", "litellm_summary")

    return success

def ask_ai(instruction:str, log:str, file:str) -> str:
    """ Send the failed project to AI for further explanations. """
    response = litellm_client.chat.completions.create(model="gpt-4.1", messages = [
        {
            "role": "user",
            "content": f"{instruction}{log}"
        }
    ])

    litellm = open(f"tmp/{file}", "a")
    litellm.write(response.choices[0].message.content)
    litellm.close()
            
def build_apps(sample_apps: dict, sample_apps_root: str, output_dir: str) -> list: 
    """ Take the parsed XML-content, generate projects, and compile them. Failures are collected using regexes. """
    failed_generations = []
    # failed_builds = []
    # warning_builds = []

    successful_generation_dir = 'tmp/successful_generations'

    successful_builds_dir = output_dir + '/successful_builds'
    failed_generations_dir = output_dir + '/failed_generations'
    failed_builds_dir = output_dir + '/failed_builds'
    
    for app_path in sample_apps:

        # app_name ex: soc_advertising_manufacturer_specific_data
        slcp_path = sample_apps_root + '/' + app_path
        app_name = app_path.split('/')[-1].split('.')[0]

        # app_parent_dir_failure = failed_generations_dir + '/'+ app_name
        # os.makedirs(app_parent_dir_failure)
        
        # Iterate over the projects supported boards
        boards = sample_apps[app_path]
        for board in boards:
            project_name = app_name + '_' + board
            # Try to generate the project and handle failure
            try:
                app_board_specific_path = successful_generation_dir + '/' + app_name + '/' + project_name
                print(f"generating {project_name} ")
                generate_project(slcp_path, app_board_specific_path, board)
            except subprocess.CalledProcessError as e:
                app_board_specific_path = failed_generations_dir + '/' + app_name + '/' + project_name
                handle_generation_error(e, app_board_specific_path,failed_generations)
                print(f"failed to generate project {project_name}, details logged at {app_board_specific_path}")
                continue
            #generation succeeded, proceeed to build
            try:
                cmake_path = app_board_specific_path + '/' + project_name + '_cmake'
                compile_project(cmake_path)
                copytree(cmake_path+'/build/default_config',successful_builds_dir + '/'+ app_name + '/' + project_name)
            except subprocess.CalledProcessError as e:
                app_board_specific_path = failed_builds_dir + '/' + app_name + '/' + project_name
                handle_build_error(e, app_board_specific_path)
                print(f"failed to build project {project_name}, details logged at {app_board_specific_path}")
                           
    
    return 0, 0, 0 
             
    #         # Check if generation_success_pattern can be found in the log, else start building
    #         if not GENERATION_SUCCESS_PATTERN.search(stdout):  
    #             failed_generations.append(project_name)
    #             print(f"Generating {project_name} failed!\n")
    #             # instruction = f"Do not incorporate the SLF4J logger warning. Incorporate the project name: {project_name}. Analyse the following project generation log and determine the reason of its failure in two to three sentences: "
    #             # ask_ai(instruction, stdout, "litellm_failed_generations")
    #         # If generations succeeds, build the application
    #         else:
    #             stdout = compile_project(project_path)
    #             print(stdout)
                
    #             if BUILD_ERROR_PATTERN.search(stdout):
    #                 failed_builds.append(project_name)
    #                 print(f"Building {project_name} failed!\n")
    #                 # instruction = f"Do not incorporate the SLF4J logger warning. Incorporate the project name: {project_name}. Analyse the following project build log and determine the reason of its failure in two to three sentences: "
    #                 # ask_ai(instruction, stdout, "litellm_failed_builds")
    #                 continue
                
    #             if BUILD_WARNING_PATTERN.search(stdout):
    #                 warning_builds.append(project_name)
    #                 print(f"Warning in {project_name}!\n")
    # return failed_generations, failed_builds, warning_builds

if __name__ == "__main__":
    sample_apps_root = sys.argv[1]
    print("Parsing the template file...")
    predefined_examples = [] if sys.argv[2].strip() == "" else sys.argv[2].split(',')
    predefined_boards = [] if sys.argv[3].strip() == "" else sys.argv[3].split(',')
    output_dir = sys.argv[4]
    # litellm_client = openai.OpenAI(api_key=sys.argv[4], base_url="https://litellm.silabs.net/")
    xml_file = sample_apps_root + '/templates.xml'
    sample_apps = parse_xml(xml_file, predefined_examples, predefined_boards)
    print("Done.")
    print("Building apps...")
    failed_generations, failed_builds, warning_builds = build_apps(sample_apps=sample_apps, sample_apps_root=sample_apps_root, output_dir=output_dir)
    # success = check_final_results(failed_generations, failed_builds, warning_builds)
    # if success == True: 
    #     print("SUCCESS")
    # else:
    #     raise Exception("FAILURE")