import os
import json
import unittest
from localstack.utils import testutil
from localstack.utils.aws import aws_stack
from localstack.utils.common import load_file, clone, retry
from localstack.services.awslambda import lambda_api
from localstack.services.awslambda.lambda_api import LAMBDA_RUNTIME_PYTHON36

THIS_FOLDER = os.path.dirname(os.path.realpath(__file__))
TEST_LAMBDA_PYTHON = os.path.join(THIS_FOLDER, 'lambdas', 'lambda_environment.py')
TEST_LAMBDA_NAME_1 = 'lambda_sfn_1'
TEST_LAMBDA_NAME_2 = 'lambda_sfn_2'
TEST_RESULT_VALUE = 'testresult1'
STATE_MACHINE_NAME = 'test_sm_1'
STATE_MACHINE_DEF = {
    'Comment': 'Hello World example',
    'StartAt': 'step1',
    'States': {
        'step1': {
            'Type': 'Task',
            'Resource': '__tbd__',
            'Next': 'step2'
        },
        'step2': {
            'Type': 'Task',
            'Resource': '__tbd__',
            'ResultPath': '$.result_value',
            'End': True
        }
    }
}
TEST_LAMBDA_NAME_3 = 'lambda_map_sfn_3'
MAP_STATE_MACHINE_NAME = 'test_map_sm_3'
MAP_STATE_MACHINE_DEF = {
    'Comment': 'Hello Map State',
    'StartAt': 'ExampleMapState',
    'States': {
        'ExampleMapState': {
            'Type': 'Map',
            'Iterator': {
                'StartAt': 'CallLambda',
                'States': {
                    'CallLambda': {
                        'Type': 'Task',
                        'Resource': '__tbd__',
                        'End': True
                    }
                }
            },
            'End': True
        }
    }
}
MAP_STATE_MACHINE_OUTPUT = [
    {
        'Hello': 'Bob'
    },
    {
        'Hello': 'Meg'
    },
    {
        'Hello': 'Joe'
    }
]
MAP_STATE_MACHINE_INPUT = [
    {
        'map': 'Bob'
    },
    {
        'map': 'Meg'
    },
    {
        'map': 'Joe'
    }
]


class TestStateMachine(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.lambda_client = aws_stack.connect_to_service('lambda')
        cls.s3_client = aws_stack.connect_to_service('s3')
        cls.sfn_client = aws_stack.connect_to_service('stepfunctions')

        zip_file = testutil.create_lambda_archive(
            load_file(TEST_LAMBDA_PYTHON),
            get_content=True,
            runtime=LAMBDA_RUNTIME_PYTHON36
        )
        testutil.create_lambda_function(
            func_name=TEST_LAMBDA_NAME_1,
            zip_file=zip_file,
            runtime=LAMBDA_RUNTIME_PYTHON36,
            envvars={'Hello': TEST_RESULT_VALUE}
        )
        testutil.create_lambda_function(
            func_name=TEST_LAMBDA_NAME_2,
            zip_file=zip_file,
            runtime=LAMBDA_RUNTIME_PYTHON36,
            envvars={'Hello': TEST_RESULT_VALUE}
        )
        testutil.create_lambda_function(
            func_name=TEST_LAMBDA_NAME_3,
            zip_file=zip_file,
            runtime=LAMBDA_RUNTIME_PYTHON36,
            envvars={'Hello': 'Replace Value'}
        )

    @classmethod
    def tearDownClass(cls):
        cls.lambda_client.delete_function(FunctionName=TEST_LAMBDA_NAME_1)
        cls.lambda_client.delete_function(FunctionName=TEST_LAMBDA_NAME_2)

    def test_create_run_map_state_machine(self):
        state_machines_before = self.sfn_client.list_state_machines()['stateMachines']

        role_arn = aws_stack.role_arn('sfn_role')
        definition = clone(MAP_STATE_MACHINE_DEF)
        lambda_arn_3 = aws_stack.lambda_function_arn(TEST_LAMBDA_NAME_3)
        definition['States']['ExampleMapState']['Iterator']['States']['CallLambda']['Resource'] = lambda_arn_3
        definition = json.dumps(definition)
        result = self.sfn_client.create_state_machine(
            name=MAP_STATE_MACHINE_NAME, definition=definition, roleArn=role_arn)

        # assert that the SM has been created
        state_machines_after = self.sfn_client.list_state_machines()['stateMachines']
        self.assertEqual(len(state_machines_after), len(state_machines_before) + 1)

        # run state machine
        state_machines = self.sfn_client.list_state_machines()['stateMachines']
        sm_arn = [m['stateMachineArn'] for m in state_machines if m['name'] == MAP_STATE_MACHINE_NAME][0]
        result = self.sfn_client.start_execution(stateMachineArn=sm_arn, input=json.dumps(MAP_STATE_MACHINE_INPUT))
        self.assertTrue(result.get('executionArn'))

        def check_invocations():
            self.assertIn(lambda_arn_3, lambda_api.LAMBDA_EXECUTOR.function_invoke_times)
            # assert that the result is correct
            response = self.sfn_client.list_executions(stateMachineArn=sm_arn)
            execution = response['executions'][0]
            result = self.sfn_client.get_execution_history(executionArn=execution['executionArn'])
            events = sorted(result['events'], key=lambda event: event['id'])
            result = json.loads(events[-1]['executionSucceededEventDetails']['output'])
            self.assertEqual(result, MAP_STATE_MACHINE_OUTPUT)

        # assert that the lambda has been invoked by the SM execution
        retry(check_invocations, sleep=0.7, retries=2)

        # clean up
        self.sfn_client.delete_state_machine(stateMachineArn=sm_arn)

    def test_create_run_state_machine(self):
        state_machines_before = self.sfn_client.list_state_machines()['stateMachines']

        # create state machine
        role_arn = aws_stack.role_arn('sfn_role')
        definition = clone(STATE_MACHINE_DEF)
        lambda_arn_1 = aws_stack.lambda_function_arn(TEST_LAMBDA_NAME_1)
        lambda_arn_2 = aws_stack.lambda_function_arn(TEST_LAMBDA_NAME_2)
        definition['States']['step1']['Resource'] = lambda_arn_1
        definition['States']['step2']['Resource'] = lambda_arn_2
        definition = json.dumps(definition)
        result = self.sfn_client.create_state_machine(
            name=STATE_MACHINE_NAME, definition=definition, roleArn=role_arn)

        # assert that the SM has been created
        state_machines_after = self.sfn_client.list_state_machines()['stateMachines']
        self.assertEqual(len(state_machines_after), len(state_machines_before) + 1)

        # run state machine
        state_machines = self.sfn_client.list_state_machines()['stateMachines']
        sm_arn = [m['stateMachineArn'] for m in state_machines if m['name'] == STATE_MACHINE_NAME][0]
        result = self.sfn_client.start_execution(stateMachineArn=sm_arn)
        self.assertTrue(result.get('executionArn'))

        def check_invocations():
            self.assertIn(lambda_arn_1, lambda_api.LAMBDA_EXECUTOR.function_invoke_times)
            self.assertIn(lambda_arn_2, lambda_api.LAMBDA_EXECUTOR.function_invoke_times)
            # assert that the result is correct
            response = self.sfn_client.list_executions(stateMachineArn=sm_arn)
            execution = response['executions'][0]
            result = self.sfn_client.get_execution_history(executionArn=execution['executionArn'])
            events = sorted(result['events'], key=lambda event: event['id'])
            result = json.loads(events[-1]['executionSucceededEventDetails']['output'])
            self.assertEqual(result['result_value'], {'Hello': TEST_RESULT_VALUE})

        # assert that the lambda has been invoked by the SM execution
        retry(check_invocations, sleep=0.7, retries=25)

        # clean up
        self.sfn_client.delete_state_machine(stateMachineArn=sm_arn)
