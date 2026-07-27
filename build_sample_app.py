
import sys
import os
import xml.etree.cElementTree as ET
import subprocess
from contextlib import chdir
from shutil import copytree



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

def handle_generation_error(e: subprocess.CalledProcessError, project_path: str) -> None:
    os.makedirs(project_path)
    with open(f"{project_path}/generation_error.log", "w") as f:
        f.write(e.stderr)

def handle_build_error(e: subprocess.CalledProcessError, project_name: str) -> None:
    os.makedirs(project_name)
    with open(f"{project_name}/build_error.log", "w") as f:
        f.write(e.stdout)

def compile_project(cmake_path:str):
    """ Compile the project using make. """
    project_name = os.path.basename(os.path.dirname(cmake_path))
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

def find_cmake_path(project_path: str, project_name: str) -> str:
    """Return the generated CMake directory for current or legacy SLC layouts."""
    candidates = [
        os.path.join(project_path, "cmake_gcc"),
        os.path.join(project_path, project_name + "_cmake"),
    ]
    for candidate in candidates:
        if os.path.isdir(candidate):
            return candidate
    raise FileNotFoundError(
        f"No generated CMake directory found. Checked: {', '.join(candidates)}"
    )
            
def build_apps(sample_apps: dict, sample_apps_root: str, output_dir: str) -> None: 
    """ Take the parsed XML-content, generate projects, and compile them. Failures are collected using regexes. """
    successful_generation_dir = 'tmp/successful_generations'
    successful_builds_dir = output_dir + '/successful_builds'
    failed_generations_dir = output_dir + '/failed_generations'
    failed_builds_dir = output_dir + '/failed_builds'
    
    for app_path in sample_apps:

        # app_name ex: soc_advertising_manufacturer_specific_data
        slcp_path = sample_apps_root + '/' + app_path
        app_name = app_path.split('/')[-1].split('.')[0]
        
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
                handle_generation_error(e, app_board_specific_path)
                print(f"failed to generate project {project_name}, details logged at {app_board_specific_path}")
                continue
            #generation succeeded, proceeed to build
            try:
                cmake_path = find_cmake_path(app_board_specific_path, project_name)
                compile_project(cmake_path)
                copytree(cmake_path+'/build/default_config',successful_builds_dir + '/'+ app_name + '/' + project_name)
            except subprocess.CalledProcessError as e:
                app_board_specific_path = failed_builds_dir + '/' + app_name + '/' + project_name
                handle_build_error(e, app_board_specific_path)
                print(f"failed to build project {project_name}, details logged at {app_board_specific_path}")



if __name__ == "__main__":
    sample_apps_root = sys.argv[1]
    print("Parsing the template file...")
    predefined_examples = [] if sys.argv[2].strip() == "" else sys.argv[2].split(',')
    predefined_boards = [] if sys.argv[3].strip() == "" else sys.argv[3].split(',')
    output_dir = sys.argv[4]
    xml_file = sample_apps_root + '/templates.xml'
    sample_apps = parse_xml(xml_file, predefined_examples, predefined_boards)
    print("Done.")
    print("Building apps...")
    build_apps(sample_apps=sample_apps, sample_apps_root=sample_apps_root, output_dir=output_dir)
