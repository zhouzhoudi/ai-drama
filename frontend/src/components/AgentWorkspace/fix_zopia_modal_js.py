import re

with open('/Users/zhoumi/ai-drama-system/frontend/src/components/AgentWorkspace/ProjectSetupModal.js', 'r') as f:
    content = f.read()

# Replace classNames to match Zopia CSS
content = content.replace('project-setup-modal-content', 'project-setup-modal-content zopia-modal')
content = content.replace('className="engine-card', 'className="engine-card')
content = content.replace('className={`engine-card', 'className={`engine-card')
content = content.replace('className="ratio-card', 'className="ratio-card')
content = content.replace('className={`ratio-card', 'className={`ratio-card')

# Update the button text to match exactly
content = re.sub(r'>确认开始<', r'>开始与 Agent 对话 🚀<', content)

with open('/Users/zhoumi/ai-drama-system/frontend/src/components/AgentWorkspace/ProjectSetupModal.js', 'w') as f:
    f.write(content)
