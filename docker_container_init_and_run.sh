#!/bin/bash

DOCS="AGENTS.md CLAUDE.md company_research cover_letters cv documents job_scraper templates upskill"

### if user mounts thier own volume on /jobs/, then populate/use it 
if [ -d /job/ ];then

    ### save copy of src files while "tricking" claude 
    ### to use the ones saved by the user through symlinks

    mkdir src
    for file in $DOCS; do 
        user_file="/job/$file"
        if ! [ -e "$user_file" ];then
            cp -r "$file" "$user_file"
        fi
        mv "$file" "src/$file"
        ln -sf "$user_file" "$file"
    done 
fi
### if /job/ does not exist, then we will assume the user is just having a 
### quick local session within the container 
claude 




