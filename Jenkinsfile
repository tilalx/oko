def startCheck(String name, String summary) {
    publishChecks name: name,
        title: name,
        summary: summary,
        status: 'IN_PROGRESS',
        detailsURL: env.BUILD_URL
}

def reportCheck(String name, String conclusion, String summary, String text = '') {
    publishChecks name: name,
        title: name,
        summary: summary,
        text: text,
        status: 'COMPLETED',
        conclusion: conclusion,
        detailsURL: env.BUILD_URL
}

def tailLog(String path, int maxLines = 300) {
    if (!fileExists(path)) {
        return ''
    }
    def lines = readFile(path).split('\n')
    def start = Math.max(0, lines.size() - maxLines)
    return "```\n${lines[start..-1].join('\n')}\n```"
}

pipeline {
    agent any

    options {
        timestamps()
        disableConcurrentBuilds()
        buildDiscarder(logRotator(numToKeepStr: '50'))
    }

    triggers {
        cron('H * * * *')
    }

    environment {
        ENTSOE_TOKEN = credentials('oko-entsoe-token')
        IMAGE_TAG = "oko:${env.BUILD_NUMBER}"
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Lint & Type-Check') {
            steps {
                script { startCheck('lint', 'Linting and type-checking...') }
                sh """
                    mkdir -p logs
                    ( docker build --target lint -t oko:lint-${env.BUILD_NUMBER} . \
                        ; echo \$? > logs/lint.status ) 2>&1 | tee logs/lint.log
                    status=\$(cat logs/lint.status)
                    if [ "\$status" -ne 0 ]; then exit "\$status"; fi
                """
            }
            post {
                success { script { reportCheck('lint', 'SUCCESS', 'Lint and type-check passed.', tailLog('logs/lint.log')) } }
                failure { script { reportCheck('lint', 'FAILURE', 'Lint or type-check failed.', tailLog('logs/lint.log')) } }
                aborted { script { reportCheck('lint', 'ABORTED', 'Lint aborted.', tailLog('logs/lint.log')) } }
                always  { sh "docker rmi oko:lint-${env.BUILD_NUMBER} || true" }
            }
        }

        stage('Test') {
            steps {
                script { startCheck('test', 'Running test suite...') }
                sh """
                    mkdir -p logs
                    ( docker build --target test -t oko:test-${env.BUILD_NUMBER} . \
                        ; echo \$? > logs/test.status ) 2>&1 | tee logs/test.log
                    status=\$(cat logs/test.status)
                    if [ "\$status" -ne 0 ]; then exit "\$status"; fi
                """
            }
            post {
                success { script { reportCheck('test', 'SUCCESS', 'Test suite passed.', tailLog('logs/test.log')) } }
                failure { script { reportCheck('test', 'FAILURE', 'Test suite failed.', tailLog('logs/test.log')) } }
                aborted { script { reportCheck('test', 'ABORTED', 'Test run aborted.', tailLog('logs/test.log')) } }
                always  { sh "docker rmi oko:test-${env.BUILD_NUMBER} || true" }
            }
        }

        stage('Publish Dataset') {
            when {
                allOf {
                    branch 'main'
                    triggeredBy 'TimerTrigger'
                }
            }
            options { timeout(time: 10, unit: 'MINUTES') }
            steps {
                script { startCheck('publish-dataset', 'Publishing forecast dataset...') }
                sh "docker build --target runtime -t ${IMAGE_TAG} ."
                sh '''
                    mkdir -p logs
                    ( set -e
                      git clone --depth 1 git@github.com:tilalx/oko-dataset.git oko-dataset

                      docker run --rm \
                        -e ENTSOE_TOKEN=$ENTSOE_TOKEN \
                        -e SQLITE_PATH=/output/oko.sqlite3 \
                        -v "$WORKSPACE/oko-dataset:/output:z,U" \
                        -v oko-history-data:/app/data \
                        $IMAGE_TAG \
                        oko.pipeline --export /output/forecast_de.json

                      cd oko-dataset
                      git add -A
                      if git diff --cached --quiet; then
                          echo "No forecast produced this run -- nothing to publish."
                          exit 0
                      fi
                      git -c user.name=oko-bot -c user.email=oko-bot@users.noreply.github.com \
                          commit -m "data: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
                      git push origin main
                    ) 2>&1 | tee logs/publish-dataset.log
                    echo $? > logs/publish-dataset.status
                    status=$(cat logs/publish-dataset.status)
                    if [ "$status" -ne 0 ]; then exit "$status"; fi
                '''
            }
            post {
                success  { script { reportCheck('publish-dataset', 'SUCCESS', 'Dataset published to oko-dataset.', tailLog('logs/publish-dataset.log')) } }
                failure  { script { reportCheck('publish-dataset', 'FAILURE', 'Dataset publish failed.', tailLog('logs/publish-dataset.log')) } }
                aborted  { script { reportCheck('publish-dataset', 'ABORTED', 'Dataset publish aborted.', tailLog('logs/publish-dataset.log')) } }
                notBuilt { script { reportCheck('publish-dataset', 'SKIPPED', 'Not an hourly main-branch run; publish skipped.') } }
                always   { sh "docker rmi ${IMAGE_TAG} || true" }
            }
        }

        stage('Build Server Image') {
            when {
                allOf {
                    branch 'main'
                    not { triggeredBy 'TimerTrigger' }
                }
            }
            options { timeout(time: 10, unit: 'MINUTES') }
            steps {
                script { startCheck('build-server-image', 'Building oko-serve image...') }
                sh """
                    mkdir -p logs
                    ( docker build --target serve \
                        -t oko-serve:${env.BUILD_NUMBER} -t oko-serve:latest . \
                        ; echo \$? > logs/build-server-image.status ) 2>&1 | tee logs/build-server-image.log
                    status=\$(cat logs/build-server-image.status)
                    if [ "\$status" -ne 0 ]; then exit "\$status"; fi
                """
            }
            post {
                success  { script { reportCheck('build-server-image', 'SUCCESS', 'oko-serve image built.', tailLog('logs/build-server-image.log')) } }
                failure  { script { reportCheck('build-server-image', 'FAILURE', 'oko-serve image build failed.', tailLog('logs/build-server-image.log')) } }
                aborted  { script { reportCheck('build-server-image', 'ABORTED', 'oko-serve image build aborted.', tailLog('logs/build-server-image.log')) } }
                notBuilt { script { reportCheck('build-server-image', 'SKIPPED', 'Not a main-branch code push; build skipped.') } }
            }
        }
    }

    post {
        failure {
            echo 'Pipeline fehlgeschlagen. Siehe obige Logs für Details.'
        }
        always {
            cleanWs()
        }
    }
}
