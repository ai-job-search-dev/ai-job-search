### use alpine to reduice image size 
FROM alpine:latest 
ARG DEBIAN_FRONTEND=noninteractive

RUN apk update && \
    apk add \
    bash \
    curl \
    poppler-utils \
    python3 \
    nodejs \
    npm \
    unzip 


### uncomment this for latex compilation, but a much larger docker image 
### needs to happen for resume and cover letter creation, but is otherwise 
### functional
# RUN apk add texlive-full

## install claude 
RUN npm install -g @anthropic-ai/claude-code

## install bun 
RUN (curl -fsSL https://bun.sh/install | bash)
## copy all code to container


WORKDIR /app 
COPY . .

CMD bash /app/docker_container_init_and_run.sh



### USE THE FOLLOWING COMMAND TO BUILD THE IMAGE
# docker build . -t ai-job-search:latest 

### USE THE FOLLOWING COMMAND TO RUN AND 
### SAVE DATA IN CURRENT USER FOLDER
# docker run -it  \
#   -e ANTHROPIC_API_KEY="GET YOUR OWN KEY AND DO NOT USE MINE " \
#   -v $(pwd):/job \
#   ai-job-search:latest 


