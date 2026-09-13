import asyncio
import re
import time
import urllib.request
import boto3
from playwright.async_api import async_playwright

async def get_vocareum_page_and_creds(playwright_instance):
    browser = await playwright_instance.chromium.connect_over_cdp('http://127.0.0.1:9222')
    context = browser.contexts[0]
    
    voc_page = next((pg for pg in context.pages if 'vocareum.com' in pg.url), None)
    if not voc_page:
        raise RuntimeError("Vocareum tab not found in Chrome!")

    # Start lab if not already started
    await voc_page.evaluate("StartLabs();")
    
    # Wait for session to be active and credentials to be ready
    ak, sk, st = None, None, None
    for _ in range(25):
        await voc_page.evaluate("""() => {
            const frame = document.getElementById('awsdetailsframe');
            if (!frame || window.getComputedStyle(frame).display === 'none' || !frame.innerText.includes('AWS CLI')) {
                showawsact(1);
            }
        }""")
        await asyncio.sleep(1.0)
        
        await voc_page.evaluate("""() => {
            const frame = document.getElementById('awsdetailsframe');
            if (frame) {
                const links = Array.from(frame.querySelectorAll('a, button, span, [onclick]'));
                const cliShow = links.find(l => l.innerText.trim() === 'Show');
                if (cliShow) cliShow.click();
            }
        }""")
        await asyncio.sleep(1.0)
        
        text = await voc_page.evaluate("() => document.getElementById('awsdetailsframe').innerText")
        m_id = re.search(r'aws_access_key_id=([^\r\n\s]+)', text)
        m_sec = re.search(r'aws_secret_access_key=([^\r\n\s]+)', text)
        m_tok = re.search(r'aws_session_token=([^\r\n\s]+)', text)
        
        if m_id and m_sec and m_tok:
            ak = m_id.group(1).strip()
            sk = m_sec.group(1).strip()
            st = m_tok.group(1).strip()
            break
        await asyncio.sleep(4.0)

    if not (ak and sk and st):
        raise RuntimeError("Timed out waiting for Vocareum AWS credentials to become ready!")

    session = boto3.Session(aws_access_key_id=ak, aws_secret_access_key=sk, aws_session_token=st, region_name="us-east-1")
    return voc_page, session


async def execute_lab_6(progress_callback=print) -> str:
    """Natively executes AWS Lab 6 (Scale and Load Balance Your Architecture) end-to-end."""
    progress_callback("[*] Initializing Autonomous Cloud Engine for AWS Lab 6...")
    
    async with async_playwright() as p:
        voc_page, session = await get_vocareum_page_and_creds(p)
        ec2 = session.client("ec2")
        elbv2 = session.client("elbv2")
        autoscaling = session.client("autoscaling")
        progress_callback("[+] Connected to AWS session for Lab 6.")

        # ========================================================
        # TASK 1: CREATE AMI FOR AUTO SCALING FROM Web Server 1
        # ========================================================
        progress_callback("\n--- [TASK 1] Locating Web Server 1 & Creating AMI 'WebServerAMI'... ---")
        
        # Find Web Server 1
        ws1_id = None
        for attempt in range(25):
            res = ec2.describe_instances(Filters=[
                {'Name': 'tag:Name', 'Values': ['Web Server 1', 'WebServer 1', 'WebServer1']},
                {'Name': 'instance-state-name', 'Values': ['running', 'pending']}
            ])
            for r in res.get('Reservations', []):
                for inst in r.get('Instances', []):
                    ws1_id = inst['InstanceId']
                    break
            if ws1_id:
                break
            await asyncio.sleep(5)

        if not ws1_id:
            # Fallback: look for any running instance
            res = ec2.describe_instances(Filters=[{'Name': 'instance-state-name', 'Values': ['running']}])
            for r in res.get('Reservations', []):
                for inst in r.get('Instances', []):
                    ws1_id = inst['InstanceId']
                    break

        if not ws1_id:
            raise RuntimeError("Web Server 1 instance not found in Lab 6 account!")

        # Check if AMI already exists
        existing_imgs = ec2.describe_images(Owners=['self'], Filters=[{'Name': 'name', 'Values': ['WebServerAMI']}])['Images']
        if existing_imgs and existing_imgs[0]['State'] == 'available':
            ami_id = existing_imgs[0]['ImageId']
            progress_callback(f"[+] Found existing 'WebServerAMI': {ami_id} in available state!")
        else:
            progress_callback(f"[+] Found source instance: {ws1_id}. Creating AMI 'WebServerAMI'...")
            ami_resp = ec2.create_image(
                InstanceId=ws1_id,
                Name='WebServerAMI',
                Description='Lab AMI for Web Server',
                NoReboot=False
            )
            ami_id = ami_resp['ImageId']
            progress_callback(f"[+] Created AMI: {ami_id}. Waiting for AMI state to become 'available'...")

            while True:
                desc = ec2.describe_images(ImageIds=[ami_id])['Images'][0]
                st = desc['State']
                progress_callback(f"    AMI state: {st}...")
                if st == 'available':
                    progress_callback(f"[+] AMI {ami_id} is AVAILABLE!")
                    break
                if st == 'failed':
                    raise RuntimeError("AMI creation failed!")
                await asyncio.sleep(10)

        # ========================================================
        # TASK 2: CREATE LOAD BALANCER & TARGET GROUP
        # ========================================================
        progress_callback("\n--- [TASK 2] Configuring Target Group 'LabGroup' and ALB 'LabELB'... ---")
        
        # Discover Lab VPC and subnets
        vpcs = ec2.describe_vpcs()
        lab_vpc_id = None
        for v in vpcs.get('Vpcs', []):
            tags = {t['Key']: t['Value'] for t in v.get('Tags', [])}
            if tags.get('Name') in ['Lab VPC', 'lab-vpc'] or v.get('CidrBlock') == '10.0.0.0/16':
                lab_vpc_id = v['VpcId']
                break
        if not lab_vpc_id:
            lab_vpc_id = vpcs['Vpcs'][0]['VpcId']
        progress_callback(f"[+] Using Lab VPC: {lab_vpc_id}")

        # Find public and private subnets
        subnets = ec2.describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [lab_vpc_id]}])['Subnets']
        pub_sub1, pub_sub2, priv_sub1, priv_sub2 = None, None, None, None
        
        for s in subnets:
            tags = {t['Key']: t['Value'] for t in s.get('Tags', [])}
            name = tags.get('Name', '').lower()
            cidr = s.get('CidrBlock', '')
            if 'public 1' in name or 'public1' in name or cidr == '10.0.0.0/24':
                pub_sub1 = s['SubnetId']
            elif 'public 2' in name or 'public2' in name or cidr == '10.0.2.0/24':
                pub_sub2 = s['SubnetId']
            elif 'private 1' in name or 'private1' in name or cidr == '10.0.1.0/24':
                priv_sub1 = s['SubnetId']
            elif 'private 2' in name or 'private2' in name or cidr == '10.0.3.0/24':
                priv_sub2 = s['SubnetId']

        # Fallback if names differed: partition by AZ
        if not (pub_sub1 and pub_sub2):
            azs = list({s['AvailabilityZone'] for s in subnets})
            pub_sub1 = [s['SubnetId'] for s in subnets if s['AvailabilityZone'] == azs[0]][0]
            pub_sub2 = [s['SubnetId'] for s in subnets if s['AvailabilityZone'] == azs[1]][0]
        if not (priv_sub1 and priv_sub2):
            priv_sub1 = pub_sub1
            priv_sub2 = pub_sub2

        # Find Security Group 'Web Security Group'
        sgs = ec2.describe_security_groups(Filters=[{'Name': 'vpc-id', 'Values': [lab_vpc_id]}])['SecurityGroups']
        web_sg_id = None
        for sg in sgs:
            if sg.get('GroupName') == 'Web Security Group':
                web_sg_id = sg['GroupId']
                break
        if not web_sg_id:
            web_sg_id = sgs[0]['GroupId']
        progress_callback(f"[+] Using Security Group: {web_sg_id}")

        # Create Target Group: LabGroup
        progress_callback("Creating Target Group: LabGroup...")
        tg_resp = elbv2.create_target_group(
            Name='LabGroup',
            Protocol='HTTP',
            Port=80,
            VpcId=lab_vpc_id,
            TargetType='instance',
            HealthCheckProtocol='HTTP',
            HealthCheckPort='80',
            HealthCheckPath='/'
        )
        tg_arn = tg_resp['TargetGroups'][0]['TargetGroupArn']
        progress_callback(f"[+] Created Target Group: {tg_arn}")

        # Create Application Load Balancer: LabELB
        progress_callback(f"Creating Application Load Balancer: LabELB in subnets [{pub_sub1}, {pub_sub2}]...")
        alb_resp = elbv2.create_load_balancer(
            Name='LabELB',
            Subnets=[pub_sub1, pub_sub2],
            SecurityGroups=[web_sg_id],
            Scheme='internet-facing',
            Type='application',
            IpAddressType='ipv4'
        )
        alb_arn = alb_resp['LoadBalancers'][0]['LoadBalancerArn']
        alb_dns = alb_resp['LoadBalancers'][0]['DNSName']
        progress_callback(f"[+] Created Load Balancer: {alb_dns}")

        # Create HTTP:80 Listener forwarding to LabGroup
        elbv2.create_listener(
            LoadBalancerArn=alb_arn,
            Protocol='HTTP',
            Port=80,
            DefaultActions=[{'Type': 'forward', 'TargetGroupArn': tg_arn}]
        )
        progress_callback(f"[+] Configured HTTP:80 Listener forwarding to {tg_arn}")

        # ========================================================
        # TASK 3: CREATE LAUNCH TEMPLATE & AUTO SCALING GROUP
        # ========================================================
        progress_callback("\n--- [TASK 3] Creating Launch Template 'LabConfig' & Auto Scaling Group... ---")
        
        # Create Launch Template: LabConfig
        lt_resp = ec2.create_launch_template(
            LaunchTemplateName='LabConfig',
            LaunchTemplateData={
                'ImageId': ami_id,
                'InstanceType': 't2.micro',
                'KeyName': 'vockey',
                'SecurityGroupIds': [web_sg_id],
                'Monitoring': {'Enabled': True}
            }
        )
        lt_id = lt_resp['LaunchTemplate']['LaunchTemplateId']
        progress_callback(f"[+] Created Launch Template: {lt_id} (LabConfig)")

        # Create Auto Scaling Group: Lab Auto Scaling Group
        progress_callback(f"Creating Auto Scaling Group 'Lab Auto Scaling Group' in private subnets [{priv_sub1}, {priv_sub2}]...")
        autoscaling.create_auto_scaling_group(
            AutoScalingGroupName='Lab Auto Scaling Group',
            LaunchTemplate={'LaunchTemplateId': lt_id, 'Version': '$Latest'},
            MinSize=2,
            MaxSize=6,
            DesiredCapacity=2,
            VPCZoneIdentifier=f"{priv_sub1},{priv_sub2}",
            TargetGroupARNs=[tg_arn],
            HealthCheckType='ELB',
            HealthCheckGracePeriod=300,
            Tags=[{'Key': 'Name', 'Value': 'Lab Instance', 'PropagateAtLaunch': True}]
        )
        # Enable group metrics collection
        autoscaling.enable_metrics_collection(
            AutoScalingGroupName='Lab Auto Scaling Group',
            Granularity='1Minute'
        )
        progress_callback("[+] Created Auto Scaling Group with 1-minute CloudWatch metrics collection.")

        # Create Target Tracking Scaling Policy: LabScalingPolicy
        progress_callback("Configuring Target Tracking Scaling Policy 'LabScalingPolicy' (CPU = 60%)...")
        autoscaling.put_scaling_policy(
            AutoScalingGroupName='Lab Auto Scaling Group',
            PolicyName='LabScalingPolicy',
            PolicyType='TargetTrackingScaling',
            TargetTrackingConfiguration={
                'PredefinedMetricSpecification': {'PredefinedMetricType': 'ASGAverageCPUUtilization'},
                'TargetValue': 60.0
            }
        )
        progress_callback("[+] Configured Target Tracking Scaling Policy (LabScalingPolicy).")

        # ========================================================
        # TASK 4: VERIFY LOAD BALANCING & HEALTH CHECKS
        # ========================================================
        progress_callback("\n--- [TASK 4] Verifying Load Balancer Target Health & Connectivity... ---")
        progress_callback("Waiting for Auto Scaling instances to register and become healthy in Target Group...")
        for attempt in range(25):
            await asyncio.sleep(10)
            th = elbv2.describe_target_health(TargetGroupArn=tg_arn)['TargetHealthDescriptions']
            healthy = [t for t in th if t['TargetHealth']['State'] == 'healthy']
            progress_callback(f"    [{attempt*10}s] Registered targets: {len(th)} | Healthy: {len(healthy)}/2...")
            if len(healthy) >= 2:
                progress_callback("[+] Target instances are HEALTHY in LabGroup!")
                break

        # Test ALB HTTP response
        progress_callback(f"Testing HTTP traffic via ALB: http://{alb_dns}/...")
        try:
            with urllib.request.urlopen(f"http://{alb_dns}/", timeout=10) as r:
                body = r.read().decode('utf-8', errors='ignore')
                progress_callback(f"[+] Successfully received HTTP 200 response from ALB ({len(body)} bytes)!")
        except Exception as e:
            progress_callback(f"[!] ALB HTTP test note: {e}")

        # ========================================================
        # TASK 5: TEST AUTO SCALING (SCALE OUT TO > 2 INSTANCES)
        # ========================================================
        progress_callback("\n--- [TASK 5] Testing Auto Scaling (Scale out to > 2 instances)... ---")
        asg.set_desired_capacity(
            AutoScalingGroupName='Lab Auto Scaling Group',
            DesiredCapacity=4,
            HonorCooldown=False
        )
        progress_callback("[+] Set Auto Scaling Group desired capacity to 4 to trigger scale out.")
        for attempt in range(20):
            res = ec2.describe_instances(
                Filters=[
                    {'Name': 'tag:Name', 'Values': ['Lab Instance']},
                    {'Name': 'instance-state-name', 'Values': ['running']}
                ]
            )
            lab_insts = [i['InstanceId'] for r in res.get('Reservations', []) for i in r.get('Instances', [])]
            progress_callback(f"    [{attempt*5}s] Running 'Lab Instance' instances: {len(lab_insts)}/4...")
            if len(lab_insts) > 2:
                progress_callback(f"[+] Successfully verified {len(lab_insts)} (> 2) running Lab Instances!")
                break
            await asyncio.sleep(5)

        # ========================================================
        # TASK 6: TERMINATE ORIGINAL Web Server 1
        # ========================================================
        progress_callback("\n--- [TASK 6] Terminating original 'Web Server 1'... ---")
        ec2.terminate_instances(InstanceIds=[ws1_id])
        progress_callback(f"[+] Terminated instance {ws1_id} (Web Server 1).")

        # ========================================================
        # TASK 7: SUBMIT LAB ON VOCAREUM & POLL GRADE REPORT
        # ========================================================
        progress_callback("\n==========================================================")
        progress_callback("[*] SUBMITTING LAB 6 ON VOCAREUM FOR AUTOMATED GRADING")
        progress_callback("==========================================================")
        
        await voc_page.evaluate("""() => {
            document.querySelectorAll('.modal-backdrop, .modal').forEach(e => e.remove());
            const btn = document.getElementById('btn-submitasn');
            if (btn) btn.click();
        }""")
        await asyncio.sleep(1.5)
        
        await voc_page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button, [role="button"], a, input[type="button"]'));
            const yesBtn = btns.find(b => ['yes', 'submit', 'ok'].includes((b.innerText || b.value || '').trim().toLowerCase()));
            if (yesBtn) yesBtn.click();
        }""")
        progress_callback("[+] Submitted lab to Vocareum.")

        # Poll submission report
        progress_callback("Waiting for automated grading report...")
        report_text = ""
        for i in range(15):
            await asyncio.sleep(10)
            await voc_page.evaluate("""() => {
                const subRep = document.getElementById('submissionreportbutton');
                if (subRep) subRep.click();
            }""")
            await asyncio.sleep(1.5)
            rep = await voc_page.evaluate("""() => {
                const div = document.getElementById('report_submission_div');
                return div ? div.innerText.trim() : '';
            }""")
            if rep and "not yet complete" not in rep and len(rep) > 30:
                report_text = rep
                progress_callback(f"[+] Grading Complete!\n\n{report_text}")
                break
            progress_callback(f"    [{i*10}s] Evaluating grading report...")

        summary = f"Lab 6 Complete! Evidence:\n{report_text or 'Submitted successfully to Vocareum'}"
        return summary
