FROM node:26

ARG TZ
ENV TZ="$TZ"

ARG CLAUDE_CODE_VERSION=latest

# Install basic development tools and iptables/ipset
RUN apt-get update && apt-get install -y --no-install-recommends \
  less \
  git \
  procps \
  sudo \
  fzf \
  zsh \
  man-db \
  unzip \
  gnupg2 \
  gh \
  iptables \
  ipset \
  iproute2 \
  dnsutils \
  aggregate \
  openjdk-25-jdk \
  maven \
  jq \
  nano \
  vim \
  build-essential \
  gcc-arm-none-eabi \
  binutils-arm-none-eabi \
  gcc-arm-linux-gnueabi \
  binutils-arm-linux-gnueabi \
  libc6-dev-armel-cross \
  gcc-arm-linux-gnueabihf \
  binutils-arm-linux-gnueabihf \
  libncurses-dev \
  pkg-config \
  bc \
  bison \
  flex \
  libssl-dev \
  libelf-dev \
  dwarves \
  device-tree-compiler \
  u-boot-tools \
  swig \
  python3 \
  python3-dev \
  python3-setuptools \
  python3-pyelftools \
  python3-pip \
  libgnutls28-dev \
  uuid-dev \
  cpio \
  kmod \
  rsync \
  lzop \
  lz4 \
  zstd \
  && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN pip install unicorn --break-system-packages

# Ensure default node user has access to /usr/local/share
RUN mkdir -p /usr/local/share/npm-global && \
  chown -R node:node /usr/local/share

ARG USERNAME=node

# Persist bash history.
RUN SNIPPET="export PROMPT_COMMAND='history -a' && export HISTFILE=/commandhistory/.bash_history" \
  && mkdir /commandhistory \
  && touch /commandhistory/.bash_history \
  && chown -R $USERNAME /commandhistory

# Set `DEVCONTAINER` environment variable to help with orientation
ENV DEVCONTAINER=true

# Create workspace and config directories and set permissions
RUN mkdir -p /workspace /home/node/.claude && \
  chown -R node:node /workspace /home/node/.claude

WORKDIR /workspace

# Set up non-root user
USER node

# Install global packages
ENV NPM_CONFIG_PREFIX=/usr/local/share/npm-global
ENV PATH=$PATH:/usr/local/share/npm-global/bin

# Set the default shell to zsh rather than sh
ENV SHELL=/bin/zsh

# Install Claude
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}


# Copy and set up firewall script
COPY init-firewall.sh /usr/local/bin/
USER root
RUN chmod +x /usr/local/bin/init-firewall.sh && \
  echo "node ALL=(root) NOPASSWD: /usr/local/bin/init-firewall.sh" > /etc/sudoers.d/node-firewall && \
  chmod 0440 /etc/sudoers.d/node-firewall

# work/, dl/ and build/ are case-sensitive Docker volumes (see devcontainer.json);
# fresh volumes are root-owned, so node may hand exactly these three to itself.
RUN echo "node ALL=(root) NOPASSWD: /usr/bin/chown node\:node /workspace/work /workspace/dl /workspace/build" > /etc/sudoers.d/node-volumes && \
  chmod 0440 /etc/sudoers.d/node-volumes
USER node